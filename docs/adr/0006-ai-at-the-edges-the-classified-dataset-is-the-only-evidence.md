# 0006. Runtime AI at the edges: the classified dataset is the only evidence, and a verifier sits before display

- Status: Accepted (owner-directed change of direction). Does not supersede
  [0001](0001-no-versioned-release.md): nothing here is tagged or released, and the first
  release still owes the hardened workflow 0001 describes.
- Date: 2026-08-21
- Deciders: Chelsea Kelly-Reif

## Context

Until this decision the project contained no model of any kind. The README said so twice, the
conformance table declared the AI Evaluation standard N/A, and `docs/RESPONSIBLE-TECH-AUDITS.md`
recorded "no LLM or model component; every classification is a deterministic rule with a
committed rationale". All of that was true and was a deliberate position: the project's whole
argument is that absence rendered as a value is the defect, and a language model is a machine
for rendering absences as fluent values.

The owner has directed that the project add real, runtime AI — a question-answering layer that
lets a prospective student or a family understand what an institution does and does not
disclose, and why the distinction matters — without the tool becoming the performance ranker it
exists to critique. The direction is recorded here as a direction *change*, because it is one,
and because an ADR is the only record in this repository that a reader can date and argue with.

What makes this dangerous is exactly what makes the project valuable. The five classifications
(`reported`, `implausible`, `suppressed`, `not_applicable`, `missing`) are five different
facts about an absence, and a model asked "does this college publish a graduation rate?" will
happily collapse them into "no". A model asked "is this a good college?" will happily answer.
Both answers would be fluent, plausible, and exactly the two things the project was built to
refuse. The prompt cannot be the control. The control has to be code that the model's output
passes through before a reader sees it, and an evaluation suite that measures the two failures
directly and is committed with its results.

## Decision

Add a runtime question-answering layer in a separate, optional subpackage
(`disclosed.ask`), with the model confined to two edges and the project's own data as the only
evidence in between.

1. **The model structures the question; it does not answer it from memory.** A question is
   turned into a typed lookup: which institution, which field or fields, which kind of question
   (what is not reported, what changed, is a value real, why would a field be suppressed, what
   does a field mean, something outside disclosure). The lookup runs against an evidence store
   built deterministically from the committed artifacts — every institution's five-way
   classification for every graded field, in every committed snapshot, plus the field-level
   drift measurements and the cross-source contradictions the project already publishes.
   Nothing is retrieved from the model's memory of what a college is like.

2. **The model sees classifications, never performance values.** The evidence handed to the
   narration step carries the classification of each field and, for `implausible` values only,
   the published value that earned that classification (a 0% admission rate is the finding; the
   number is the evidence). For `reported` fields the value is withheld from the model
   altogether. The model cannot narrate a graduation rate it was never shown, and the tool does
   not become a ranker by accident.

3. **Every substantive claim cites a record, and a verifier checks it before display.** The
   narration step returns claims, each citing one or more evidence records by id (institution,
   source, snapshot, field, classification). A programmatic verifier resolves each citation
   against the evidence pack that was actually supplied, checks that any classification word in
   the claim agrees with the cited record's classification (a claim that says "suppressed" over
   a `missing` record is wrong in precisely the way this project exists to name), and withholds
   every claim that fails. The count of withheld claims is shown. A response consisting only of
   withheld claims is shown as a refusal, not as silence.

4. **Performance judgement is refused, in code and in the prompt, and measured.** Questions
   asking which college is better, how to rank a set, whether to attend, or what an outcome
   "really" is are answered with a refusal that says what the tool grades instead. The refusal
   is checked by a dedicated adversarial evaluation over many phrasings (direct, indirect,
   comparative, embedded in a legitimate question, "just tell me") scored on whether the output
   contains any quality ordering, recommendation, or performance judgement. The tolerance is
   zero, and the number is committed.

5. **The five states are never collapsed, and that is measured too.** A second evaluation
   supplies ground-truth classifications and scores, per state, whether the narration rendered
   the wrong one — "they have no graduation rate" over a `not_applicable` record counts as a
   failure, as does treating an `implausible` zero as a real value. This is the portfolio's
   "absence rendered as a value" defect class in its purest form, and it gets its own number.

6. **Field definitions are quoted from the federal sources, never paraphrased.** A committed
   `corpus/` holds the IPEDS and College Scorecard documentation for every field the project
   classifies, with retrieval dates and hashes. A definition in an answer is a verbatim quote
   the verifier confirms is present in the corpus; a definition that does not verify is
   withheld like any other claim.

7. **Drift is narrated from the project's own measurement and keeps its direction.** The
   project measures drift within one source over time, in both directions (a field gained as
   well as lost), as a change in the share of applicable institutions reporting, and refuses to
   compare IPEDS against the Scorecard because their field sets do not overlap. The narration
   inherits every one of those constraints: direction is read from the rate the project
   computed, never recomputed by the model; sources are never mixed; a field the project did
   not measure is "not measured", never "unchanged".

8. **Honest refusals for everything else.** An institution not in the frame, a field not
   classified for that institution, a question outside disclosure (safety, housing,
   accreditation, cost of living) — the answer says so and points at what is known. Nothing
   fills a gap.

Consequential choices:

- **Provider and model.** The public `anthropic` SDK, `claude-sonnet-5` as the configurable
  default. The credential comes from the environment only; the code never writes a key to any
  file. Amazon Bedrock is supported through the same SDK so evaluations can run on whichever
  route the owner's credentials reach, and every committed evaluation result names the
  provider, model, prompt version, commit and date it came from. A result without that
  provenance is rejected by a test. A number that was not measured live is recorded as "not
  run", never estimated.
- **The static site stays static by default.** Without an endpoint configured at build time,
  the site is byte-for-byte what it was: no scripts, no subresources, no request to anywhere
  but itself. With one, institution pages gain an explicit opt-in control; until a reader
  submits a question, the page makes no off-origin request, and a test proves it from the
  built bytes. A failed or rate-limited request leaves the page as it was.
- **Cost is bounded from the first commit.** Per-client rate limits, a hard daily cap, and
  prompt caching on the stable system prompt. A 429 is a complete, readable answer to a reader,
  not an error page.
- **Deployment is a separate decision.** The service is prepared as a Lambda handler behind a
  Function URL with CORS locked to the Pages origin and a cost bound, as an unapplied template.
  No infrastructure is provisioned by this decision. Exposing the service to the public is a
  deployment with its own cost envelope, abuse review and subprocessor record, and it is
  recorded as pending the owner's call.

## Consequences

- The README's "nothing in the dataset or on the site is model-generated" narrows to the
  dataset and the static pages, which remain true; the AI Evaluation row in the conformance
  table changes from N/A to Applies; `docs/RESPONSIBLE-TECH-AUDITS.md` gets an append-only
  addendum rather than an edit. Every one of those rewrites lands in the same series of changes
  as the code that makes them necessary, not before and not after.
- AI output is always labelled as AI-generated, unofficial, and about disclosure rather than
  quality. A disclosure grade is not a quality grade, and the label says so on every answer.
- The evaluation harness and its cases are committed and run in `make verify` against a fake
  provider (so the verifier, the refusal policy, and the five-state fidelity checks are gated on
  every push with no key and no network), and separately against a live provider when one is
  reachable, with the results committed under provenance.
- The project's coverage gate, type gate and lint gate apply to the new subpackage unchanged.
  `anthropic` becomes the project's first runtime dependency, behind an optional extra, so the
  grading pipeline and the static site still install with none.
- ADR 0001 is untouched. Nothing is tagged. The first release still owes the hardened workflow.

## Alternatives considered

- **Keep the project model-free.** The position this ADR replaces. Rejected by the owner as a
  product decision; recorded here so the reason it was held is not lost.
- **Let the model read the raw federal records and answer freely.** Rejected. A model with
  the values in front of it will be asked "what is their graduation rate compared to X" and
  will answer; withholding the values is the only control that does not depend on the prompt.
- **Retrieval over embeddings.** Not needed. The evidence is keyed by institution and field;
  the lookup is exact, inspectable, and has no second provider dependency.
- **Build-time generation of per-institution narratives.** Considered and deferred. It would
  keep the site static and reviewable, but it answers no reader's actual question, and the
  owner's direction is a question-answering layer. It remains a possible future use of the
  same verifier.
