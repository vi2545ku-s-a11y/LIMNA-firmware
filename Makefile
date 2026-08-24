# LIMNA — genvagar. Skriv "make" for att se allt.
#
# Simulatorn och pollaren ska koras i VAR SITT fonster, precis som i README.
# Darfor finns "make sim" och "make poll" som separata mal.

.PHONY: help install selftest up down sim poll factory status clean

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
	@echo "  make down       stoppa dataplattformen"
	@echo "  make clean      radera buffer.db, config.json, faults.json"
	@echo ""
	@echo "Vanlig ordning:  make install && make selftest && make up"
	@echo "Sedan i tva fonster:  make sim   respektive   make poll"

install:
	python3 -m pip install -r requirements.txt

selftest:
	@echo "=== Sensorprofilen ==="
	python3 sensor_profiles.py --selftest
	@echo ""
	@echo "=== Vattenmodellen ==="
	python3 fake_sensors.py --selftest
	@echo ""
	@echo "=== Buffertlogiken ==="
	python3 poller.py --selftest

up:
	docker compose up -d
	@echo ""
	@echo "Grafana:  http://localhost:3000   (admin / limna)"
	@echo "Datakallan och dashboarden laggs in automatiskt."

down:
	docker compose down

sim:
	python3 fake_sensors.py

poll:
	python3 poller.py

# Etapp 8. Sensorerna svarar pa sina fabriksadresser, med adresskrocken kvar,
# sa att adressbytet gar att ova innan monteringsdagen. I ett annat fonster:
#   python3 set_address.py --list
#   python3 set_address.py --scan
factory:
	python3 fake_sensors.py --factory

status:
	@echo "=== Containrar ==="
	@docker compose ps 2>/dev/null || echo "  docker svarar inte"
	@echo ""
	@echo "=== Python-processer ==="
	@pgrep -fl "fake_sensors.py|poller.py" || echo "  inga igang"
	@echo ""
	@echo "=== Buffert ==="
	@test -f buffer.db && python3 -c "import sqlite3; c=sqlite3.connect('buffer.db'); t,p=c.execute(\"SELECT COUNT(*), SUM(CASE WHEN sent=0 THEN 1 ELSE 0 END) FROM readings\").fetchone(); print(f'  {t or 0} matningar, {p or 0} oskickade')" || echo "  buffer.db finns inte an"

clean:
	rm -f buffer.db buffer.db-journal config.json faults.json
	@echo "Lokala filer raderade. De skapas pa nytt vid nasta korning."
