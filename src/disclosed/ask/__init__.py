"""The optional runtime question-answering layer (ADR 0006).

Everything in this subpackage is bounded by one rule: the project's own classified records are
the only evidence, and a model is allowed to do exactly two things with them -- turn a reader's
question into a lookup, and narrate what the lookup returned, citing each record it narrates.
A verifier sits between the narration and the reader.

Nothing here is imported by the grading pipeline or the static site generator. The project
installs and runs with none of it; ``disclosed[ask]`` adds the one runtime dependency it needs.
"""
