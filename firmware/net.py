"""
LIMNA firmware - WiFi, clock and MQTT. The only file that knows about the link.

firmware/README.md asks for the transport to be swappable without touching
the measurement cycle: when WiFi is replaced by LoRa or LTE-M, this file is
replaced and main.py is not. That is why sending is one function,

    send(lines, cfg) -> how many the broker confirmed

and why nothing in here measures anything.

Credentials live in config.json on the board, which is never committed.
"""

import time


def wifi(cfg, timeout_s=15):
    """Connect if not already connected. Returns True when connected."""
    import network
    w = network.WLAN(network.STA_IF)
    w.active(True)
    if w.isconnected():
        return True
    w.connect(cfg["wifi"]["ssid"], cfg["wifi"]["password"])
    start = time.time()
    while time.time() - start < timeout_s:
        if w.isconnected():
            print("  [wifi]     ansluten, ip", w.ifconfig()[0])
            return True
        time.sleep(0.5)
    print("  [wifi]     ingen anslutning -- matningen sparas och skickas senare")
    return False


def clock_valid():
    """False until NTP has set the clock. Before that time.gmtime() says 2000."""
    return time.gmtime()[0] >= 2025


def sync_clock():
    try:
        import ntptime
        ntptime.settime()
        print("  [klocka]   stalld via NTP")
        return True
    except Exception as exc:
        print("  [klocka]   NTP misslyckades:", exc)
        return False


def send(lines, cfg):
    """Publish each line with QoS 1. Returns how many the broker confirmed.

    umqtt.simple waits for the broker's PUBACK on a QoS 1 publish, so a line
    that returns without an exception has been confirmed. The first failure
    stops the batch: what was confirmed stays confirmed, the rest waits for
    the next cycle.
    """
    from umqtt.simple import MQTTClient
    m = cfg["mqtt"]
    client = MQTTClient("limna-" + cfg["buoy_id"], m["host"],
                        port=m.get("port", 1883))
    confirmed = 0
    try:
        client.connect()
        for line in lines:
            client.publish(m["topic"], line, qos=1)
            confirmed += 1
    except Exception as exc:
        print("  [sandning] avbruten efter %d: %s" % (confirmed, exc))
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
    return confirmed
