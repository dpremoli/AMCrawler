# AMCrawler

Watches Avventure nel Mondo for departures that still need a coordinator and tells its owner which ones newly appeared.

## Language

### The app's user

**Owner**:
The one Coordinator the app works for; the Digest Filter, Hidden Departures and Digest are theirs. Others may sign in to maintain it but have no settings of their own.
_Avoid_: user, account, subscriber

### The catalogue

**Itinerary**:
A trip design in the Avventure nel Mondo catalogue (e.g. "Egitto Soft", code `1410`), independent of any date. Italian: *viaggio*.
_Avoid_: base, trip, tour

**Departure**:
One dated running of an Itinerary with its own code (e.g. `2026-0041`, 29 Oct – 7 Nov). Italian: *partenza*.
_Avoid_: trip, tour, group, viaggio

**Formula**:
The product line an Itinerary belongs to (Adventure, Discovery, Trekking, Soft, Motoraid, ...).
_Avoid_: category, type, line

**Area**:
One of the site's geographic regions an Itinerary is filed under (e.g. "Sud Est Asiatico").
_Avoid_: region, zone

**Country**:
A country an Itinerary visits. Sub-national places the site lists as their own entry (Sicilia, Alaska, Tibet, ...) are folded into their parent Country.
_Avoid_: nation, destination

### People

**Coordinator**:
The volunteer traveller who leads a Departure. Italian: *coordinatore*.
_Avoid_: tour leader, guide, leader

**Participant**:
A person booked on a Departure. Italian: *iscritto*.
_Avoid_: pax, booking, traveller

### What the app watches

**Coordinator-free Itinerary**:
An Itinerary designed to run without a Coordinator (e.g. "Ghana Volunteer"). Its Departures are never Uncovered.

**Uncovered Departure**:
A Departure of a non-Coordinator-free Itinerary that has no Coordinator, at least one Participant, and is not full. Italian: *partenza senza coordinatore*.
_Avoid_: open trip, trip without coordinator, free departure

**Digest Filter**:
The saved criteria (Area, Country, Formula, departure window in days from today, duration, minimum Participants) that decide which Uncovered Departures the Digest considers.
_Avoid_: notification settings, preferences, alert

**Search**:
The criteria, and optional free text, currently applied while browsing; changing it never affects the Digest. The text is never part of the Digest Filter.
_Avoid_: filter (unqualified), query

**New Entry**:
An Uncovered Departure that matches today's Digest Filter now but did not match that same Digest Filter at the previous Digest. A Departure can become a New Entry more than once (e.g. its Coordinator drops out).
_Avoid_: new trip, new departure, update

**Hidden Departure**:
A Departure the owner has ruled out; it is excluded from Search results and the map and can never be a New Entry.
_Avoid_: dismissed, archived, deleted

**Digest**:
The email listing New Entries, sent on the Owner's chosen weekdays while enabled. On a day with no New Entries it is skipped, unless the Owner asked for a "nessuna novità" mail. After a pause or skipped days, the next Digest reports every New Entry since the previous Digest.
_Avoid_: newsletter, summary, report, riepilogo
