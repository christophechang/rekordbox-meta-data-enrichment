# Rekordbox Metadata Enrichment: Filling the Gaps

A DJ library is only as useful as its metadata. Rekordbox auto-analyses BPM and key reliably on import. Label, year, remixer and album are a different story. For a library built over years from Bandcamp purchases, SoundCloud downloads, promo folders and other sources, those fields are often blank, inconsistent or outright wrong.

That matters because metadata is not cosmetic. It shapes how MixLab reasons about the music. A track tagged with a label, a year and a recognisable release context tells a much richer story than one with empty fields.

---

## The Problem

MixLab, the AI mix curation tool in this ecosystem, uses metadata as contextual input when building concepts and narratives. "Classic Warp era" means something. "Metalheadz 2019 reissue" means something. "Label: blank" means nothing.

In a library of more than 2,500 tracks, over a thousand were missing label entirely. Many had year set to `0`. Album values were inconsistent — some were genuine release names, some were Apple Music style single titles, some were little more than the original download folder. The information existed somewhere. The library was not carrying it.

Both MusicBrainz and Discogs hold large release databases. The challenge was not whether the data existed. The challenge was matching the right release metadata to the right Rekordbox tracks in a safe, repeatable way.

---

## The Solution

**Rekordbox Metadata Enrichment** is a Python CLI that reads a Rekordbox XML export, looks up each track against MusicBrainz and Discogs, scores candidate matches by confidence, and writes a delta XML containing only the tracks that changed, ready to import back into Rekordbox.

A few design decisions shaped the tool from the start:

- **Delta output, not full library replacement.** The output XML contains only tracks that changed. Once the cache is warm, the export can be very small, which keeps Rekordbox import fast and low risk.
- **Colour as confidence signal.** Rather than silently applying guesses, the Rekordbox `Colour` field is repurposed as a visual confidence indicator. Green means high confidence and safe to trust. Orange means an LLM was used to disambiguate. Red means low-confidence or heuristic metadata and worth checking manually. Blank means nothing was found.
- **Permanent cache.** Metadata for a processed track does not need to be looked up again. Once resolved, it is stored and reused on later runs.
- **Failures are never cached.** Tracks with no match are always retried on future runs. This turned out to be an important decision because every improvement in query logic automatically recovers previously missed tracks without needing cache cleanup.

At a high level, the pipeline for each track is:

1. Check cache
2. Skip if already complete
3. Query MusicBrainz
4. Score candidates
5. Query Discogs if needed
6. Score again
7. Auto-enrich if confidence is high
8. Use an LLM only if the result is still ambiguous
9. Apply confidence colour
10. Export only changed tracks

LLM disambiguation follows the same conventions as MixLab and TuneFinder. Mistral is preferred first, then Groq, then Gemini. The model is used only to resolve ambiguity between grounded candidate matches. If it cannot make a confident decision, the track is left unenriched rather than guessed at.

---

## The Journey

The implementation went through a few iterations, but three phases mattered most.

### Phase 1: A healthy run with the wrong result

The first full run looked promising on paper. Hundreds of tracks were enriched and there were no API errors. But opening the export XML showed something was clearly wrong: every `Label` field was still blank.

The report confirmed it. Year and album were being updated, but not label. That pointed to the real issue: Discogs, the source expected to do most of the heavy lifting for underground electronic music, was not actually contributing anything.

The problem was a bad query filter. Discogs had been given a literal format string that matched nothing, so every lookup silently returned zero results. One line fixed it. With the format restriction removed, Discogs immediately began returning useful label data.

The architecture was sound, but one bad assumption had effectively disabled the most important source.

### Phase 2: Making confidence visible

With label data flowing, the next question was how to expose uncertainty safely. Leaving everything quietly applied at the same apparent confidence level was not good enough — the library needed to distinguish between results worth trusting and results worth checking.

The answer was to surface confidence inside Rekordbox itself using the `Colour` field. High-confidence results could be accepted immediately. Lower-confidence results were still applied but now stood out clearly for manual review. Colour confidence moved from optional to default.

A small heuristic layer also helped with tracks that no public database was likely to cover — bootlegs, white labels, unofficial edits. Those now receive meaningful fallback values instead of staying blank.

The key shift here was treating uncertainty as information rather than something to hide. The tool stopped pretending every result was equally trustworthy and gave the library a practical review workflow to act on.

### Phase 3: Better queries, fewer misses

With the main bug fixed and confidence made visible, the remaining challenge was recall.

The unresolved list showed clear patterns:
- multi-artist entries were being queried too literally
- mix designators and feature credits were polluting titles
- BPM or key fragments had leaked into some names
- catalogue numbers were embedded in track titles
- built-in Rekordbox demo tracks were being sent to live APIs for no reason

Improving artist and title normalisation recovered a significant number of previously missed tracks. Primary artist fallback, stripping feature credits, removing mix markers, cleaning trailing metadata and excluding demo content all pushed the enrichment rate higher.

The most useful improvement, though, was not a query tweak. It was turning unresolved tracks into a first-class output. Instead of burying them in a text report, the tool now exports playlists that let unresolved tracks be reviewed directly inside Rekordbox. That made the process far easier to inspect and act on.

---

## Numbers

| Run | Enriched | No Match | Change |
|-----|----------|----------|--------|
| 1, Discogs effectively disabled | 436 | 801 | Baseline |
| 2, Discogs fixed | 506 | 613 | +70 enriched |
| 3, Colour confidence + heuristics | 655 | 628 | +149 enriched |
| 4, Query improvements | 715 | 535 | +60 enriched |
| 5, Additional normalisation fixes | 876 | 354 | +161 enriched |

A few headline numbers from the latest run:

- **2,354 tracks** processed, excluding SoundCloud entries and Rekordbox demo tracks
- **876 tracks enriched**
- **354 unresolved**
- **Around 65 LLM calls** per full run
- **Zero API errors** across all runs
- **Around 90 minutes** for a cold run, near-instant when the cache is warm

The enrichment rate is not perfect, but that is part of the point. The tool is designed to prefer trustworthy results over aggressive guesswork.

---

## Why the LLM is There at All

This is not an AI-first tool. It is a metadata pipeline built on deterministic sources.

MusicBrainz and Discogs do the real work. The LLM is used only when multiple plausible candidates remain and a lightweight reasoning step can help decide between them. That keeps the process grounded, cost-aware and explainable.

This also follows the same pattern used elsewhere in the ecosystem: lower-cost models for first-pass work, stronger models held back for the smaller number of cases where deeper reasoning genuinely adds value.

---

## What's Next

There are still obvious areas to improve.

**Concurrent processing.** The current pipeline processes one track at a time. It works, but a bounded concurrent model would reduce cold-run time significantly.

**Smarter artist normalisation.** Real libraries contain messy metadata: duplicated feature credits, inconsistent spacing, stray territory markers and artist separators used in unpredictable ways. Cleaning more of that before lookup should recover additional matches.

**Detecting swapped artist and title.** A small number of tracks have the fields reversed entirely. Those are hard to match automatically and are better treated as review candidates unless detected up front.

**Richer metadata.** Label was the main target, but release year, release title and remixer are all useful to MixLab as contextual signals. The enrichment pipeline makes those fields much easier to trust and use.

**Tighter MixLab integration.** This was the whole point. Once enriched XML is imported back into Rekordbox, MixLab has a richer view of the collection and can reason about scenes, eras, labels and release context far more effectively.

---

## The Ecosystem

This tool is one part of a wider system built around a single Rekordbox library and a long history of recorded mixes:

- **TuneFinder** discovers new music weekly, ranking candidates from Beatport, Juno, Bandcamp and other sources against a taste profile inferred from years of recorded mixes
- **Rekordbox Metadata Enrichment** fills in the release metadata gaps so the library is more useful to both Rekordbox and MixLab
- **MixLab** uses that enriched library to generate structured mix concepts with narrative arcs, surfacing forgotten tracks and sequencing them for harmonic compatibility
- **The Mixes API** catalogues recorded SoundCloud mixes, including tracklists, dates, moods and transitions, making the back catalogue queryable as structured data
- **Changsta.com** is the public-facing DJ site, with an AI search layer that lets visitors explore mixes and music in natural language

Each layer feeds the next. Better discovery builds a richer library. Better metadata produces stronger mix concepts. A structured back catalogue makes the history searchable. The aim throughout has been consistent: spend less time managing music, more time playing it, and make the whole ecosystem easier to explore.
