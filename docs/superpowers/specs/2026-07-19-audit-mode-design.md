# Audit Mode — Design Spec

Date: 2026-07-19
Status: approved — ready for implementation (phase 1 first, §11)
Repo: RekordboxMetaDataEnrichment (engine — new `--audit` mode)

## 1. Context and goal

The engine and daemon fill *blank* Label/Year (fill-blank-only, never overwrite). But a large part of the library came from torrents / Soulseek, where existing Label/Year is often wrong (mislabelled scene rips, wrong pressing year, "Various Artists" junk), and some existing values were written by the *old, buggy* version of this tool (repress-year bug, label-borrow quirk). Wrong Label/Year actively degrades MixLab — a track mis-tagged with the wrong year lands in the wrong era bucket (corrupting era-coherence scoring and the era-dialogue concept direction); a wrong label misgroups it in label-coherence.

**Goal:** a **one-off, opt-in** pass that re-derives Label/Year across the *whole* collection (not just blanks), **overwrites where the source authoritatively disagrees and the match is high-confidence**, flags the uncertain disagreements for manual review, and delivers the result as change-typed Rekordbox playlists + a full diff — reviewed and imported the same way as every other run.

This is a deliberate, scoped exception to fill-blank-only. It is **not** part of the daemon.

## 2. Decisions (locked with operator)

- **Opt-in, one-off, standalone** — a CLI flag `--audit`. The daemon **never** passes it and stays fill-blank-only forever. The two risk postures never mix.
- **Whole collection** — bypass the already-complete short-circuit; look up every eligible track (same reader exclusions as always: SoundCloud/demo/blank-artist).
- **Confidence-gated writes** — the safety core:
  - **High-confidence match (≥ the fixed audit floor) + value genuinely differs → overwrite.** We are sure it is the same track and the source is authoritative.
  - **Uncertain match + value differs → do NOT overwrite.** Flag it for manual review instead. A shaky match must never replace existing data.
  - **Blank field + any confident value → fill** (identical to normal enrichment, governed by the usual `--confidence-threshold`).
- **Fields: Label + Year.** Remixer stays fill-only (blank→value), never overwritten — low stakes. **Artist / Title / Comments are never touched** (unchanged invariant — protects MixLab's played/unplayed match and the MIK + mood-tag `Comments` contract).
- **Label comparison is normalized** — see §3.3 for the exact rule.
- **Year uses the existing rule uniformly** — remix designator → the remix's year; otherwise the earliest release year (MB `first-release-date` / Discogs master). Year is the murkier column (pressing-vs-original intent), so year overwrites are surfaced prominently for review; the `changes.json` diff is the per-track revert record. **Year overwrites on a green match, same gate as Label** (open item resolved).
- **Review UX = playlists, not a text report.** Segment changes into named Rekordbox playlists so review happens in Rekordbox (select → hear → see columns → fix in place).
- **Confidence is encoded in playlist *names* for review tracks, not in Colour** — Rekordbox has a single `Colour` attribute per track, so per-playlist colouring is not expressible (see §3.5).

## 3. Behaviour

Run: `enricher --input <xml> --audit [--sources all]` (plus the usual flags). `--audit` changes the areas below; everything else (lookup, scoring, disambiguation) is unchanged.

### 3.1 No already-complete skip

Every eligible track is looked up. The already-complete short-circuit in `enricher.py::process_track` is bypassed under `--audit`.

### 3.2 The overwrite gate (fixed floor, not `--confidence-threshold`)

`--confidence-threshold` is an unconstrained float on the CLI, so it must **not** govern destructive writes — `--audit --confidence-threshold 0.3` would otherwise auto-overwrite on junk matches.

- **`_AUDIT_OVERWRITE_FLOOR = 0.85`**, a module constant, independent of every presentation and tuning flag.
- An overwrite requires **all** of:
  1. `best.confidence >= _AUDIT_OVERWRITE_FLOOR`;
  2. the value came **directly from the winning candidate** — never a borrowed field (see §3.4);
  3. the value genuinely differs (normalized, §3.3).
- **LLM-disambiguated matches never overwrite.** A match that only survived via `disambiguate()` is by definition uncertain → flag.
- **Colour-confidence (red, `>= _COLOUR_MIN`) matches never overwrite.** That path exists to apply weak matches to *blanks*; it has no authority over existing data.
- `--confidence-threshold` continues to govern **fills** exactly as today. `--no-llm` and `--no-colour-confidence` affect fills only and cannot widen the overwrite gate.

### 3.3 Label difference rule (conservative, biased toward "not a discrepancy")

Two labels are considered **the same** (→ no overwrite, no flag) when, after normalization, either:

a. the normalized strings are equal; or
b. one's token list is a **leading-token prefix** of the other's (`sofa sound` vs `sofa sound bristol`).

Normalization, in this order:

1. casefold;
2. replace `&` with `and` (**before** punctuation stripping — otherwise `&` is deleted and `A & B` / `A and B` diverge);
3. strip remaining punctuation, collapse whitespace;
4. tokenize;
5. drop trailing corporate suffix tokens (`records`, `recordings`, `record`, `music`, `ltd`, `limited`, `inc`, `llc`) **only while at least one non-suffix token remains**.

Step 5's guard matters: without it `Music` and `Records` both normalize to an empty token list and compare equal. A label consisting solely of suffix tokens keeps its tokens verbatim.

Rule (b) is what actually carries the `Sofa Sound` / `Sofa Sound Bristol` example — the suffix-stripping in (a) alone does not, which was a hole in the previous draft. The failure mode of (b) is *under*-flagging (a genuinely wrong label that happens to be a leading prefix of the right one is left alone), never a bad write — the correct direction to fail for a destructive pass. General substring containment is explicitly **not** used.

### 3.4 No borrowed values in overwrites

`enricher.py::_fill_label` merges a label from a *different* candidate onto the winner (`model_copy`), preserving the winner's `source` and `confidence`. That is acceptable for filling a blank; it is **not** acceptable as authority to destroy an existing value, and it would misattribute the source in `changes.json`.

- The audit records, per field, which candidate the value came from.
- **Only a value from the winning candidate's own record can overwrite.** A borrowed label may still **fill a blank** (unchanged behaviour) and is recorded with the donor's source and the donor's confidence.
- Borrowed-value fills are marked in `changes.json` (`borrowed: true`) so the audit trail never claims authority it does not have.
- **Borrowing is Label-only by design.** Year is taken only from the winning candidate's own source record, or left without a suggestion. Adding borrowing for any other field requires a separate design decision and tests. (This is also what makes the §10 mixed-action case producible — the asymmetry is intentional, not incidental.)
- **A borrowed value that disagrees with a populated field is flagged into its own tier**, regardless of the donor's confidence — a donor can score ≥ 0.85 and would otherwise have no review route. Borrowed disagreements route to `Audit — review (unverified source)` (§4). The tier is assigned by *provenance*, not by score: the objection is that the value's authority is unestablished, which a high donor score does not fix.

### 3.5 Colour

Colour semantics are unchanged from a normal run: a track's `Colour` is set from its best-match confidence tier for tracks that are actually **filled or overwritten**. **Flagged-only tracks keep their existing Colour untouched** — a flag changes no metadata, including Colour. Review confidence is carried by playlist name instead (§4).

### 3.6 Cache

Cached candidates from the backfill were refined *conditionally on the track's field being blank* — MB label detail is fetched only when `not track.label`, Discogs master-year only when `not track.year` (`enricher.py`). Comparing a *populated* field against such a candidate would miss real disagreements and compare against pressing years.

- Audit uses a **v3 cache namespace**, in which both refinements run unconditionally.
- The existing v2 cache stays valid and untouched for normal runs; v3 entries are written alongside.
- The first audit run therefore performs live lookups; subsequent audit runs are warm.
- Unchanged cache invariants: empty candidate lists and API errors are never cached; corrupt files back up and rebuild.

## 4. Output — change-typed playlists

A track appears in each playlist that applies to it (a Label+Year overwrite lands in both — no combinatorial explosion):

- **`Audit — Label overwritten`** — existing label replaced.
- **`Audit — Year overwritten`** — existing year replaced.
- **`Audit — review (orange)`** — medium-confidence discrepancies, not written.
- **`Audit — review (red)`** — low-confidence discrepancies, not written.
- **`Audit — review (unverified source)`** — discrepancies whose suggested value was borrowed from a non-winning candidate (§3.4), at any confidence. Separate because the reason to distrust these is provenance, not score.
- **`Enriched — filled blanks`** — the ordinary blank-fills (Label/Year/Remixer), same as a normal run.

The review playlists *locate* the disagreements; `changes.json` carries the your-value-vs-suggested detail (Rekordbox has no native field for a suggestion, and `Comments` is untouchable). The operator imports the overwrite + enriched playlists to apply changes; the review playlists are for manual inspection (nothing to import — metadata there is unchanged).

A track that is overwritten on one field and flagged on another appears in **both** the relevant overwrite playlist and the relevant review playlist.

## 5. Per-field decision model

The current `EnrichmentDecision` has one track-level `status` and `fields_changed: dict[str, tuple[str, str]]` (old, new only). That cannot represent a track whose Label is filled while its Year is flagged, and gives the writer no safe authorization source.

Introduce a first-class per-field result:

```python
OutcomeSource = Literal["musicbrainz", "discogs", "beatport", "heuristic"]


class FieldOutcome(BaseModel):
    field: Literal["label", "year", "remixer"]
    old: str
    suggested: str = ""
    action: Literal["filled", "overwritten", "flagged", "unchanged"]
    source: OutcomeSource | None = None   # None when there is no suggestion
    confidence: float | None = None       # None when there is no suggestion
    borrowed: bool = False
```

`source` admits `"heuristic"` and is optional. Two reasons: heuristic fills (`_HEURISTIC_LABELS`) currently construct a `CandidateMatch` with `source="musicbrainz"`, which is a false attribution in an audit trail; and an `unchanged` outcome has no suggesting source at all. Implementation note: the heuristic path must set `source="heuristic"` on the outcome even though the synthetic `CandidateMatch` keeps its existing shape.

**Authorization applies to every mode, not just audit.** All decisions — audit and normal — populate `field_outcomes`; a normal run emits only `filled` and `unchanged` outcomes, since it cannot produce `overwritten` or `flagged`. The writer then authorizes universally from `field_outcomes`: it writes `filled` and `overwritten`, never `flagged` or `unchanged`. Scoping the new authorization path to audit alone would leave two write paths to keep in sync; emitting outcomes everywhere keeps one, and makes the "only" assertion literally true.

- Track-level `status` is for reporting, never for authorization.
- Playlist routing derives from `field_outcomes`.
- `fields_changed` is retained as-is for backward compatibility with existing reporting, populated from the `filled`/`overwritten` outcomes.

### 5.1 Writer preconditions (defense in depth)

One write path, but the writer does not trust the decision layer. It **re-validates every outcome against the source XML** before writing, and refuses any outcome failing its preconditions. A faulty decision therefore cannot bypass the audit invariants — the invariants are enforced at the point of the write, not only at the point of the decision.

| Action | Preconditions — all must hold, or the write is refused |
|---|---|
| `filled` | the target attribute in the source XML is blank **and** `outcome.old` is blank |
| `overwritten` | audit mode is active **and** field ∈ {Label, Year} **and** the source XML's current value equals `outcome.old` **and** `borrowed is False` **and** `confidence >= 0.85` |
| `flagged`, `unchanged` | never written, unconditionally |

The `current value == outcome.old` check is the load-bearing one: it re-reads the source attribute at write time rather than trusting the value the decision captured, so an overwrite computed against stale or mismatched state cannot land.

A refused outcome is a **bug, not a routine skip** — it is logged loudly with track id, field, and the failed precondition, and counted in the run summary. A non-zero refusal count means the decision layer and the writer disagree, which must be investigated rather than tolerated.

`_PROTECTED_ATTRS` / the existing write whitelist remain in force above all of this: Name, Artist, and Comments are unreachable regardless of any outcome.

## 6. changes.json (the diff + revert record)

Serialized from `field_outcomes`: old value, suggested value, action, source, confidence, borrowed flag.

**Scope of serialization:** only outcomes with an action of `filled`, `overwritten`, or `flagged` are written — i.e. fields where something happened or something is proposed. `unchanged` outcomes are omitted (they would triple the file with no information). Tracks whose whole run produced no outcome — `skipped_no_match`, `skipped_api_error` — are represented by their track-level `status` alone, with no field entries; they have no suggesting source and nothing to revert.

This is the full audit trail and the practical undo map (Time Machine covers audio files, not Rekordbox metadata).

## 7. Delivery

**Output paths follow the existing `--output` contract unchanged.** `--output` is a *file* path (default `export/rekordbox_export_YYYY-MM-DD.xml`), and the change list is derived from it as `<output>.changes.json` (`__main__.py`). There is no "output dir" CLI concept, and audit does not introduce a fixed filename.

`--audit` changes only the **default** value of `--output`, to `export/audit_YYYY-MM-DD.xml`, so an audit artifact is not mistaken for a routine one. An explicit `--output` always wins, in audit mode as in every other. The change list remains `<output>.changes.json` in both modes.

`enricher --audit` writes those two files and stops. Discord delivery and the Air pull live in `daemon.py` and are not reachable from a manual CLI run — the earlier draft's claim of identical delivery was wrong. The operator collects the artifacts from the `--output` location directly. A one-shot delivery command is **out of scope** and must not alter daemon behaviour if added later.

## 8. Invariants (enforced, tested)

- Artist / Title / Comments attributes are **never** modified (unchanged from the base engine).
- A field is overwritten **only** when confidence ≥ `_AUDIT_OVERWRITE_FLOOR` (0.85), the value is non-borrowed, and it genuinely differs. LLM-disambiguated and colour-confidence matches never overwrite.
- `--confidence-threshold`, `--no-llm`, `--no-colour-confidence` cannot widen the overwrite gate.
- Label "difference" is decided by the §3.3 rule — spelling variants and leading-prefix variants never trigger an overwrite.
- Flagged-only tracks are byte-identical to source, including `Colour`.
- Source XML is never modified; all outputs are separate files.
- `--audit` is inert unless explicitly passed; the daemon never passes it.

## 9. Scope

- Fields audited: Label, Year. Remixer fill-only. Nothing else.
- One-off manual operation. No daemon wiring, no scheduling, no delivery.

## Out of scope

- Label canonicalisation for consistency (rewriting every label to one source's spelling) — the audit fixes *mistakes*, not spelling variants.
- Overwriting Remixer, Album, Genre, or any Comments-derived data.
- Auto-import / direct Rekordbox DB writes — output stays an XML the operator imports.
- Discord / Air delivery for audit runs.

## 10. Testing

Existing pytest/respx style; no live calls by default.

**Overwrite gate**
- confidence 0.85+ + differing value → `overwritten`; 0.84 → `flagged`, not written; blank → `filled`.
- `--audit --confidence-threshold 0.3` + a 0.4-confidence differing match → `flagged`, **not** overwritten (the floor is independent).
- An LLM-disambiguated differing match → `flagged`, never overwritten.
- A colour-confidence (red) differing match → `flagged`, never overwritten.

**Borrowed values**
- Winner lacks a label, donor supplies one, track has an existing different label → `flagged`, not overwritten.
- Same setup with a **0.95-confidence donor** → still `flagged`, and routed to `Audit — review (unverified source)`, not to an orange/red playlist.
- Same setup but track label blank → `filled`, with `borrowed: true` and the donor's source/confidence in `changes.json`.

**Label normalization**
- `X` vs `X Records`, `Sofa Sound` vs `Sofa Sound Bristol` → not a discrepancy (no overwrite, no flag).
- Negative: `Sound` vs `Soundway` → *is* a discrepancy (token prefix, not substring — rule (b) is token-wise).
- Negative: `Hessle Audio` vs `Hemlock Recordings` → discrepancy.
- Negative: `Music` vs `Records` → discrepancy (suffix stripping must not reduce either to an empty token list).
- `A & B` vs `A and B` → not a discrepancy (`&` replacement precedes punctuation stripping).

**Year**
- Remix keeps remix year; original resolves to earliest; differing existing year on a green match → `overwritten` and listed in `Year overwritten`.

**Cache**
- A v2 cache entry does not satisfy an audit lookup; audit populates v3 with unconditional MB-label / Discogs-master-year refinement.
- A normal (non-audit) run still hits v2 and is unaffected.

**Playlist routing**
- Label+Year overwrite → both overwrite playlists.
- **Mixed actions on one track:** winner supplies its own differing Year (≥0.85 → `overwritten`) while its Label is borrowed and differs (→ `flagged`). Routes to `Audit — Year overwritten` **and** `Audit — review (unverified source)`. This is the mixed case the selection rules can actually produce — one winning candidate carries one confidence, so a candidate's *own* Label and Year always share a tier; provenance, not score, is what splits them (borrowing applies to Label only, via `_fill_label`).
- Flagged-only track → review playlist only, source-identical metadata including `Colour`.

**Writer preconditions (§5.1) — each tested by feeding the writer a deliberately faulty outcome**
- `overwritten` outcome with `borrowed=True` → refused, attribute unchanged, refusal logged.
- `overwritten` outcome with `confidence=0.80` → refused.
- `overwritten` outcome in **non-audit** mode → refused.
- `overwritten` outcome on a field outside {Label, Year} → refused.
- `overwritten` outcome whose `old` does not match the source XML's current value → refused (stale-state guard).
- `filled` outcome targeting an attribute that is **not** blank in the source → refused.
- `flagged` and `unchanged` outcomes → never written under any flag combination.
- Refusal count surfaces in the run summary and is zero across the whole normal-mode and audit-mode fixture suite.

**Invariants**
- Artist/Title/Comments byte-identical.
- Year is never borrowed: no fixture produces a Year outcome with `borrowed=True`.
- `--audit` off → behaviour identical to today (fill-blank-only, no overwrites, v2 cache, default `--output` name unchanged).
- **Normal-mode regression:** a non-audit run emits `filled`/`unchanged` outcomes and the writer still applies every fill it applies today — guards against the outcome-authorization switch silently disabling normal enrichment.
- Explicit `--output` overrides the audit default; `<output>.changes.json` derivation is unchanged in both modes.

## 11. Implementation sequence

The `field_outcomes` / writer migration touches the daemon-driven normal path; audit behaviour does not. Landing them together would make any regression ambiguous between the two. So they ship as **two checkpoints**, and the first must be provably inert.

### Phase 1 — outcome migration only (no audit behaviour)

Add `FieldOutcome`, populate `field_outcomes` on every decision, switch the writer to outcome-based authorization with the §5.1 preconditions. **No `--audit` flag, no overwrite logic, no v3 cache, no new playlists.** Phase 1 is a pure refactor with an intended behavioural delta of zero.

**Gate — all four must hold before phase 2 begins:**

1. **Normal-mode output XML is byte-identical** before and after, run against the same fixture XML. Not "equivalent filled metadata and playlists" — byte-identical, which also catches attribute ordering, whitespace, and playlist member ordering that a field-level comparison would pass. Same for the generated `.changes.json`.
2. **Zero writer refusals** across the whole fixture suite (a non-zero count means the decision layer and writer disagree — §5.1).
3. **Full existing test suite passes**, unmodified. Any test that has to change to accommodate phase 1 is evidence the refactor is not inert; investigate rather than update the test.
4. `ruff format . && ruff check . && mypy . && pytest` clean.

Capture the pre-change fixture output as a committed golden file so the byte comparison is reproducible rather than a one-off local check.

### Phase 2 — audit behaviour

Only after phase 1 is green: `--audit` flag, the overwrite gate (§3.2), label normalization (§3.3), borrowing rules (§3.4), v3 cache namespace (§3.6), the new playlists (§4), and audit-specific outcomes and routing.

Phase 2's own regression bar is that **normal mode remains byte-identical to the phase-1 golden file** — `--audit` off must change nothing, and the golden file is what proves it.

## 12. Open items

None. (Year policy resolved: green-overwrite. Output naming resolved: `--output` keeps its existing file-path contract; `--audit` only changes the default to `export/audit_YYYY-MM-DD.xml` — see §7.)
