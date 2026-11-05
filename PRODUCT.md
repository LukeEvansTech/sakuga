# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One collector (Luke) of Japanese anime production cels: Dragon Ball's Broly, Fullmetal Alchemist,
Evangelion and Pokémon. The main moment is on a phone: the 08:00 Pushover digest arrives, he taps
it, flicks through the day's new cels and opens one or two. Longer browsing of everything for sale is
occasional.

## Product Purpose

Sakuga watches Yahoo Auctions (through Buyee), Mercari and Mandarake for production cels of the
wanted franchises, keeps a catalogue of every cel seen, and sends Luke one message a day about the cels
never seen before. Success: no cel worth chasing is missed, and none is announced twice.

## Positioning

It catalogues the artwork, not the listing. Japanese sellers relist expensive cels for months or
years under new IDs and on several sites; Sakuga recognises the same cel by its photo and announces
it once. Marketplace saved searches alert on every relist.

## Operating Context

- A cel is judged first by the artwork: which character, which scene, episode and cut.
- Buying happens elsewhere: Buyee for Yahoo and Mercari, Mandarake directly.
- Prices are in yen with a pound estimate; Mercari shows pounds to UK visitors.
- Single user on the home network; installed to the phone's home screen.

## Capabilities and Constraints

- Pages: Today (new since the last digest, and the last digest), Catalogue (everything available),
  Cel (all photos and sightings, price history, notes), Log (each sweep and its sources).
- Server-rendered FastAPI + Jinja, one CSS file; no client framework.
- Listing photos are stored by the app; their quality and crop vary by seller.
- Titles are Japanese; the LLM supplies a short English title and notes, which can be missing.
- GAINAX limited reproduction cels (numbered, from 100) are the one kind alerted instantly.

## Evidence on Hand

Real listings from live sweeps only. No testimonials, users or metrics beyond the sweep log.

## Product Principles

1. The artwork comes first: photos lead every view; words support them.
2. New means new to the catalogue, never a relisting.
3. One glance on a phone answers "anything worth opening today?".
4. Say plainly when something failed or is unknown (a source down, no photo, no LLM notes).
