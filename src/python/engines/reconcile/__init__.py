"""Reconcile: the on-demand analysis module.

Reconcile is what an auditor opens when they want a document checked rather
than read. It proves, arithmetically and from the printed page alone, that the
totals a financial statement asserts are the sums it presents -- and when they
are not, says so in a sentence a reviewer can verify by hand in ten seconds.

Two rules govern everything here, and both are load-bearing:

**Structure nominates; arithmetic confirms.** A cell becomes a candidate total
only on evidence independent of its value. Arithmetic may confirm or refute a
candidate and may never propose one. Numbers added in arbitrary order tie by
coincidence more often than intuition suggests, and a module that reports a
coincidental tie as a verified total is untrusted from the first demo.

**Everything is derived from the PDF.** No XBRL, no taxonomy, no external fact
set, at build time or at runtime.

This is the fourth sibling engine and the only one that does not run in the
cache build. `engines.financial` is cache-build detection and runs on every
document; analysis code does not go there.

Plan of record: docs/reconcile.local.md.
"""
