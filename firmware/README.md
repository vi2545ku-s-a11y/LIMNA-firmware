# firmware/ — ESP32-koden

**Ännu inte påbörjad.** Den här mappen är tom med avsikt: den säger vad som
ska hit, i vilken ordning, och vilka beslut som redan är fattade. Koden i
`laptop/` är utkastet — den skrevs som en repetition av det som ska köra här.

---

## Språket är MicroPython

Inte C++, och skälet väger tyngre än prestanda: projektägaren är konstruktör,
inte utvecklare, och ska kunna läsa och ändra bojens kod om tre år. `poller.py`
går att flytta över nästan mening för mening. En omskrivning i C++ ger en fil
som ingen i projektet kan underhålla själv.

Kostnaden är ärlig. MicroPython är långsammare och drar något mer ström när
den är vaken. För en boj som är vaken två minuter per kvart och sover resten
avgörs energin av uppvärmningstiden, inte av processorn.

---

## Det som portas rakt av

Registeravkodningen, medianen av tre avläsningar, omförsöken, larmgränserna,
F-12-bedömningen, korskontrollerna `temp_d` och `do_d`, `bradskande()`. Vanlig
Python utan beroenden.

`delad/sensor_profiles.py` följer med **oförändrad**. Den kopieras till kortets
filsystem vid flashning. Det är fortfarande en enda källa för registerkartan —
skriv aldrig in ett registernummer här.

---

## Det som faktiskt är arbetet: djupsömn river minnet

`time.sleep` blir `machine.deepsleep()`. Men djupsömn är inte en paus, det är
en avstängning: vid uppvakning startar programmet om från första raden. **Varje
mätcykel blir en omstart**, och all modulnivå-state försvinner.

| Vad | I `laptop/poller.py` | Konsekvens vid djupsömn |
|---|---|---|
| `_stuck_history` | RAM | **Nollställs varje cykel — fastfrusetkontrollen slutar fungera helt** |
| `last_send` | RAM | Sändfönstret börjar om, modemet väcks varje uppvakning |
| `in_eco` | RAM | Energisparläget fastnar aldrig, F-02 blir verkningslöst |
| `_scale_cache` | RAM | Nivågivarens skalregister läses varje cykel |

Den första raden motsäger en kommentar som står i pollaren i dag: att räknaren
nollställs vid omstart beskrivs där som en medveten begränsning, "precis som på
ESP32:n efter en omstart". Det stämmer när omstarter är sällsynta. Med djupsömn
är varje cykel en omstart, och tröskeln på 24 respektive 96 mätningar — sex
timmar och ett dygn — hinner aldrig räknas upp. Kontrollen skulle se ut att
finnas och aldrig lösa ut.

**Lösningen:** RTC-minnet, som överlever djupsömn. Cirka 2 KB via
`machine.RTC().memory()`. Där ryms `last_send`, `in_eco`, skalfaktorn och en
kort tabell över senaste värdet per parameter med räknare.

Det är den enda strukturella ändringen i logiken. Resten är oförändrat.

---

## Bufferten: SQLite blir filer

Det finns ingen SQLite i MicroPython, och den behövs inte. Räkna på K-04:

    199 byte × 96 mätningar/dygn × 30 dygn ≈ 570 KB

Det är ingenting på ett SD-kort. En fil per dygn, en JSON-rad per mätning, och
en liten pekare för hur långt sändningen kommit. Skriv mätningen och `flush()`
**innan** något försök att sända — samma ordning som i dag, samma krav K-03.
Radera filer äldre än 30 dygn.

Lättare att felsöka med kortet i en läsare än en databas hade varit.

---

## Uppkopplingen — beslut som inte är fattat

Bojen har 4G, inte WiFi. Två vägar:

- **PPP mot modemet.** `network.PPP` reser en länk över UART och `umqtt`
  fungerar som vanliga sockets. Koden liknar den som redan är testad. Mer RAM,
  och TLS för K-02 blir tungt men görligt.
- **Modemets egna MQTT-kommandon.** Många moduler (SIM7600, SIM7000) klarar
  MQTT själva via AT-kommandon, inklusive TLS. Mindre RAM, men sändningen
  skrivs om mot modemets kommandouppsättning i stället för mot `paho-mqtt`.

Lutar åt PPP, för att det håller koden nära den som är bevisad. Valet hänger
ihop med vilken modem-modul som köps, alltså med EL-08.

---

## Strömrailen

`[ström på]` blir en GPIO som styr en MOSFET. `split_rail` blir två GPIO:er
(E-11, E-12). Det är den enkla delen.

Den svåra: **mät den verkliga sömnströmmen på det kort du väljer.** En bar
ESP32-modul drar runt tio mikroampere i djupsömn. Ett vanligt utvecklingskort
med USB-krets och regulator kan dra flera milliampere — tusen gånger mer — och
det äter upp hela energibudgeten utan att synas någonstans i koden. Välj kort
efter uppmätt sömnström, inte efter datablad för chippet.

`machine.WDT` ger EL-05 nästan gratis: hänger programmet startar kortet om. En
krasch mitt i en cykel är värre än ett tappat mätvärde, för då sover bojen
aldrig.

---

## Ordningen

1. ESP32 på bänken, blinka en lysdiod, **mät sömnströmmen**.
2. Modbus RTU mot **en** riktig givare, med profilen kopierad rakt av.
3. Djupsömnscykeln med RTC-minnet — mät, sov, mät, och se att räknarna
   överlever.
4. SD-kortet och bufferten.
5. Modemet och sändningen.
6. Först därefter MOSFET-railen och delningen.

Steg 1 och 3 först, eftersom de avgör om energibudgeten håller. Är
sömnströmmen fel spelar resten ingen roll.

---

## Öppen fråga

Modbus-biblioteket för MicroPython är inte pymodbus. Det finns fungerande
alternativ, men API:et skiljer sig — verifiera vilket som gäller innan koden
skrivs mot det, i stället för att gissa.
