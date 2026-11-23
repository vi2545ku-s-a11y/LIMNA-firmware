#!/usr/bin/env python3
"""
LIMNA firmware - tests that run on the laptop with ordinary Python.

Everything here is the part of the firmware that does not need the board:
Modbus frames and checksums, decoding, the buffer, the payload, and the
simulated sensors. The frames are checked against the bytes printed in the
two sensor manuals (docs/datablad/), so a pass means the code agrees with
the manufacturer -- not just with itself.

    cd firmware && python3 -m unittest -v test_firmware
"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, os.pardir, "delad"))

import sensor_profiles                                  # noqa: E402
import modbus                                           # noqa: E402
import measure                                          # noqa: E402
from buffer import Buffer                               # noqa: E402
from fakebus import FakeBus                             # noqa: E402

PROFILE = sensor_profiles.load_profile("limna-damm-2026")
SPECS = sensor_profiles.value_specs(PROFILE)
TEMPS = sensor_profiles.temp_specs(PROFILE)
PRIMARY = sensor_profiles.temp_primary_unit(PROFILE)


def hexbytes(text):
    return bytes(int(b, 16) for b in text.split())


class ManualFrames(unittest.TestCase):
    """Requests and replies exactly as printed in the manuals."""

    def test_ph_read_all(self):
        # pH manual 5.5.3: read 10 registers from 0x0000
        self.assertEqual(modbus.read_frame(1, 0x0000, 10),
                         hexbytes("01 03 00 00 00 0A C5 CD"))

    def test_turbidity_read_temp_and_turbidity(self):
        # Turbidity manual 5.3 c
        self.assertEqual(modbus.read_frame(1, 0x0100, 2),
                         hexbytes("01 03 01 00 00 02 C5 F7"))

    def test_turbidity_change_address(self):
        # Turbidity manual 5.3 (1): address 1 -> 6
        self.assertEqual(modbus.write_frame(1, 0x2000, 6),
                         hexbytes("01 06 20 00 00 06 02 08"))

    def test_ph_change_address(self):
        # pH manual 5.5.1: address 1 -> 5
        self.assertEqual(modbus.write_frame(1, 0x0200, 5),
                         hexbytes("01 06 02 00 00 05 48 71"))

    def test_ph_reply_decodes_to_manual_values(self):
        reply = hexbytes("01 03 14 09 F1 03 0F 09 5B FE 14 03 0F FE 13 "
                         "09 5B 09 5B 09 5B 09 F1 95 90")
        regs = modbus.parse_read_reply(1, reply, 10)
        by_key = {s["key"]: s for s in SPECS}
        self.assertEqual(measure.decode(regs[0x0001], 100, True), 7.83)
        self.assertEqual(measure.decode(regs[by_key["orp"]["addr"]], 10, True),
                         239.5)
        self.assertEqual(measure.decode(regs[0x0000], 100, True), 25.45)

    def test_turbidity_reply_decodes_to_manual_values(self):
        regs = modbus.parse_read_reply(1, hexbytes("01 03 04 00 C0 03 D9 3B 65"),
                                       2)
        self.assertEqual(measure.decode(regs[0], 10, False), 19.2)
        self.assertEqual(measure.decode(regs[1], 10, False), 98.5)

    def test_corrupted_reply_is_rejected(self):
        bad = bytearray(hexbytes("01 03 04 00 C0 03 D9 3B 65"))
        bad[4] ^= 0x01                                   # one bit of noise
        with self.assertRaises(modbus.ModbusError):
            modbus.parse_read_reply(1, bytes(bad), 2)

    def test_exception_reply_is_an_error_not_a_value(self):
        frame = bytes([1, 0x83, 0x02])
        crc = modbus.crc16(frame)
        with self.assertRaises(modbus.ModbusError):
            modbus.parse_read_reply(1, frame + bytes([crc & 0xFF, crc >> 8]), 1)


class Decoding(unittest.TestCase):
    def test_negative_orp(self):
        self.assertEqual(measure.decode(0xFF83, 10, True), -12.5)
        self.assertEqual(measure.decode(0xFF83, 10, False), 6541.1)


class Buffering(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.buf = Buffer(os.path.join(self.dir, "buffer"))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def _store(self, t, n):
        self.buf.store({"t": t, "n": n})

    def test_nothing_lost_across_an_outage(self):
        for i in range(5):
            self._store("2026-11-23T10:0%d:00+00:00" % i, i)
        entries = self.buf.unsent()
        self.buf.mark_sent(entries[:3])               # broker confirmed three
        self.assertEqual(self.buf.pending(), 2)
        self._store("2026-11-24T00:01:00+00:00", 99)   # next day, new file
        rest = self.buf.unsent()
        self.assertEqual([json_n(e) for e in rest], [3, 4, 99])
        self.buf.mark_sent(rest)
        self.assertEqual(self.buf.pending(), 0)

    def test_survives_restart(self):
        self._store("2026-11-23T10:00:00+00:00", 1)
        self._store("2026-11-23T10:01:00+00:00", 2)
        self.buf.mark_sent(self.buf.unsent()[:1])
        again = Buffer(self.buf.root)                  # as after a reboot
        self.assertEqual([json_n(e) for e in again.unsent()], [2])


def json_n(entry):
    import json
    return json.loads(entry[2])["n"]


class SimulatedSensors(unittest.TestCase):
    def test_reading_too_early_is_wrong_and_waiting_is_right(self):
        now = [1795449600.0]                           # a fixed instant
        bus = FakeBus(PROFILE, SPECS, TEMPS, clock=lambda: now[0])
        ph = next(s for s in SPECS if s["key"] == "ph")
        bus.power_on()
        early, _ = measure.sample(bus, ph)
        now[0] += 31                                   # past the 30 s warm-up
        late, _ = measure.sample(bus, ph)
        self.assertLess(early, 6.0)                    # far too low
        self.assertTrue(6.5 < late < 8.5)

    def test_unpowered_sensor_does_not_answer(self):
        bus = FakeBus(PROFILE, SPECS, TEMPS)
        value, fault = measure.sample(bus, SPECS[0])
        self.assertIsNone(value)
        self.assertEqual(fault, "no_response")

    def test_full_payload(self):
        now = [1795449600.0]
        bus = FakeBus(PROFILE, SPECS, TEMPS, clock=lambda: now[0])
        bus.power_on()
        now[0] += 31
        values = {s["key"]: measure.sample(bus, s)[0] for s in SPECS}
        temp, temp_d, faults = measure.thermometers(bus, TEMPS, PRIMARY)
        cfg = {"buoy_id": "LIMNA-ESP32", "buoy_name": "t"}
        p = measure.build_payload(cfg, "2026-11-23T10:00:00+00:00", 13.1,
                                  values, temp, temp_d, faults)
        self.assertEqual(sorted(k for k in p if k not in
                                ("boj", "namn", "t")),
                         ["batt", "orp", "ph", "temp", "temp_d", "turb"])
        # The two simulated offsets differ by 0.25 C. The turbidity probe's
        # register counts tenths, so rounding can move the gap by 0.05.
        self.assertAlmostEqual(temp_d, 0.25, delta=0.051)
        self.assertEqual(measure.urgent(dict(p, ph=9.4),
                                        {"ph": {"low": 6.0, "high": 9.0}}),
                         ["ph 9.4 over 9.0"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
