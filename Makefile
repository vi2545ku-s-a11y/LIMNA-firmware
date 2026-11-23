# LIMNA — genvagar. Skriv "make" for att se allt.
#
# Repot ar delat i tre mappar sedan 2026-08-24:
#
#   delad/      registerkartan. Lases av laptopkoden OCH av firmwaren
#   laptop/     laptoprepetitionen: simulator, pollare, verktyg
#   firmware/   ESP32-koden. Annu inte pabörjad, se firmware/README.md
#
# Dataplattformen (docker-compose.yml och konfigfilerna) ligger kvar i roten,
# eftersom Compose laser .env ur sin egen mapp.
#
# Simulatorn och pollaren ska koras i VAR SITT fonster, precis som i README.
# Darfor finns "make sim" och "make poll" som separata mal.

.PHONY: help install selftest up down sim poll factory status clean fwtest esp32

help:
	@echo "LIMNA"
	@echo ""
	@echo "  make install    installera Python-beroenden (pinnade versioner)"
	@echo "  make selftest   etapp 1: sensorprofil, vattenmodell och buffert"
	@echo "  make up         etapp 2: starta dataplattformen (fyra containrar)"
	@echo "  make sim        etapp 3: starta sensorsimulatorn   (eget fonster)"
	@echo "  make poll       etapp 3: starta pollaren           (eget fonster)"
	@echo "  make factory    etapp 8: simulator pa FABRIKSADRESSER, for att"
	@echo "                  ova adressbytet med set_address.py"
	@echo "  make status     visa vad som kor just nu"
	@echo "  make fwtest     firmwarens tester, pa laptopen (ingen hardvara)"
	@echo "  make esp32      kopiera firmwaren till ESP32:n och starta om den"
	@echo "  make down       stoppa dataplattformen"
	@echo "  make clean      radera buffer.db, config.json, faults.json"
	@echo ""
	@echo "Vanlig ordning:  make install && make selftest && make up"
	@echo "Sedan i tva fonster:  make sim   respektive   make poll"

install:
	python3 -m pip install -r laptop/requirements.txt

# Sjalvtesten koras FRAN laptop/. Kartkontrollen jamfor simulatorn med
# pollaren, och den hittar dem dar de ligger -- inte fran roten.
selftest:
	@echo "=== Sensorprofilen ==="
	cd laptop && python3 ../delad/sensor_profiles.py --selftest
	@echo ""
	@echo "=== Vattenmodellen ==="
	cd laptop && python3 fake_sensors.py --selftest
	@echo ""
	@echo "=== Buffertlogiken ==="
	cd laptop && python3 poller.py --selftest

up:
	docker compose up -d
	@echo ""
	@echo "Grafana:  http://localhost:3000   (admin / limna)"
	@echo "Datakallan och dashboarden laggs in automatiskt."

down:
	docker compose down

sim:
	cd laptop && python3 fake_sensors.py

poll:
	cd laptop && python3 poller.py

# Etapp 8. Sensorerna svarar pa sina fabriksadresser, med adresskrocken kvar,
# sa att adressbytet gar att ova innan monteringsdagen. I ett annat fonster:
#   cd laptop && python3 set_address.py --list
#   cd laptop && python3 set_address.py --scan
factory:
	cd laptop && python3 fake_sensors.py --factory

status:
	@echo "=== Containrar ==="
	@docker compose ps 2>/dev/null || echo "  docker svarar inte"
	@echo ""
	@echo "=== Python-processer ==="
	@pgrep -fl "fake_sensors.py|poller.py" || echo "  inga igang"
	@echo ""
	@echo "=== Buffert ==="
	@test -f laptop/buffer.db && python3 -c "import sqlite3; c=sqlite3.connect('laptop/buffer.db'); t,p=c.execute(\"SELECT COUNT(*), SUM(CASE WHEN sent=0 THEN 1 ELSE 0 END) FROM readings\").fetchone(); print(f'  {t or 0} matningar, {p or 0} oskickade')" || echo "  buffer.db finns inte an"

clean:
	rm -f laptop/buffer.db laptop/buffer.db-journal laptop/config.json laptop/faults.json
	@echo "Lokala filer raderade. De skapas pa nytt vid nasta korning."

# Firmwarens rena Python-delar, testade mot manualernas exempelramar.
fwtest:
	cd firmware && python3 -m unittest -v test_firmware

# Kopierar profilen (samma fil som laptopen laser, ingen kopia i repot) och
# firmwaren till kortet, och startar om det. config.json pa kortet, med
# WiFi-losenordet, ror den inte. Byt port med:  make esp32 PORT=/dev/cu.xxx
PORT ?= /dev/cu.usbmodem1101
MPREMOTE ?= $(shell command -v mpremote || echo $(HOME)/Library/Python/3.14/bin/mpremote)
esp32:
	$(MPREMOTE) connect $(PORT) cp delad/sensor_profiles.py firmware/modbus.py \
		firmware/fakebus.py firmware/buffer.py firmware/measure.py \
		firmware/net.py firmware/main.py : + reset
