"""
LIMNA firmware - Modbus RTU over RS485, written out by hand.

Two function codes are all the buoy needs: 0x03 (read holding registers) and
0x06 (write one register, for changing an address). Doing them here instead
of using a library keeps the frame logic small enough to read, and lets the
part that matters -- building and checking frames -- run as plain Python on
the laptop, where test_firmware.py checks it against the frames printed in
the sensor manuals.

The part that needs hardware is RTUBus at the bottom: a UART and the DE/RE
pins of the RS485 module. Nothing above it imports machine.
"""


class ModbusError(Exception):
    """The sensor answered, but not with what was asked for."""


def crc16(data):
    """Modbus CRC-16: polynomial 0xA001, start 0xFFFF. Low byte goes first."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _with_crc(body):
    crc = crc16(body)
    return bytes(body) + bytes([crc & 0xFF, crc >> 8])


def read_frame(unit, register, count=1):
    """Request for function 0x03: read `count` registers from `register`."""
    return _with_crc(bytes([unit, 0x03,
                            register >> 8, register & 0xFF,
                            count >> 8, count & 0xFF]))


def write_frame(unit, register, value):
    """Request for function 0x06: write one register. The reply is an echo."""
    return _with_crc(bytes([unit, 0x06,
                            register >> 8, register & 0xFF,
                            value >> 8, value & 0xFF]))


def parse_read_reply(unit, reply, count):
    """Check a 0x03 reply and return its registers as a list of ints.

    Every check here is one a real bus can fail: noise flips a bit (CRC), a
    second sensor on the same address answers over it (CRC again), or the
    sensor says it does not have that register (an exception reply, function
    code with 0x80 set). An exception reply is an error, never a value.
    """
    if len(reply) < 5:
        raise ModbusError("for kort svar (%d byte)" % len(reply))
    if reply[0] != unit:
        raise ModbusError("svar fran adress %d, fragade %d" % (reply[0], unit))
    if reply[1] == 0x83:
        raise ModbusError("givaren svarade med felkod %d" % reply[2])
    if reply[1] != 0x03:
        raise ModbusError("ovantad funktionskod 0x%02X" % reply[1])
    expected = 5 + 2 * count
    if len(reply) < expected or reply[2] != 2 * count:
        raise ModbusError("fel langd pa svaret")
    body, crc = reply[:expected - 2], reply[expected - 2:expected]
    if crc16(body) != crc[0] | (crc[1] << 8):
        raise ModbusError("fel kontrollsumma (CRC)")
    return [(reply[3 + 2 * i] << 8) | reply[4 + 2 * i] for i in range(count)]


def parse_write_reply(request, reply):
    """A 0x06 reply is the request echoed back, byte for byte."""
    if len(reply) >= 3 and reply[1] == 0x86:
        raise ModbusError("givaren vagrade skrivningen, felkod %d" % reply[2])
    if bytes(reply[:8]) != bytes(request):
        raise ModbusError("svaret ar inte ett eko av fragan")


class RTUBus:
    """The real bus: UART1 plus the RS485 module's DE and RE pins.

    DE high = transmit, RE low = receive (RE is active low), so they are
    switched together: both high to send, both low to listen. Pins come from
    the "pins" block in config.json, with the defaults from firmware/README.md.

    NOT YET TRIED AGAINST HARDWARE -- the RS485 module has not arrived.
    """

    def __init__(self, serie, pins, timeout_ms=200):
        from machine import UART, Pin
        parity = {"N": None, "E": 0, "O": 1}[serie["parity"]]
        self.uart = UART(pins.get("uart", 1), baudrate=serie["baudrate"],
                         bits=serie["bytesize"], parity=parity,
                         stop=serie["stopbits"],
                         tx=pins.get("tx", 17), rx=pins.get("rx", 18))
        self.de = Pin(pins.get("de", 16), Pin.OUT, value=0)
        self.re = Pin(pins.get("re", 15), Pin.OUT, value=0)
        self.timeout_ms = timeout_ms
        # Sensor power. GPIO 5 drives the rail MOSFET from step 4; until it
        # is fitted these two only log.
        self.rail_pin = pins.get("rail")

    def _transfer(self, frame, reply_len):
        import time
        self.uart.read()                       # drop anything left over
        self.de.value(1)
        self.re.value(1)
        self.uart.write(frame)
        self.uart.flush()                      # wait until it is all sent
        self.de.value(0)
        self.re.value(0)
        reply = b""
        start = time.ticks_ms()
        while len(reply) < reply_len:
            if time.ticks_diff(time.ticks_ms(), start) > self.timeout_ms:
                break
            chunk = self.uart.read()
            if chunk:
                reply += chunk
            else:
                time.sleep_ms(5)
        if not reply:
            raise OSError("svarar inte")
        return reply

    def read(self, unit, register, count=1):
        reply = self._transfer(read_frame(unit, register, count), 5 + 2 * count)
        return parse_read_reply(unit, reply, count)

    def write(self, unit, register, value):
        frame = write_frame(unit, register, value)
        parse_write_reply(frame, self._transfer(frame, 8))

    def power_on(self):
        print("  [strom pa] sensorrail")

    def power_off(self):
        print("  [strom av] sensorrail")
