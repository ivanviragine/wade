## Review is required unless the change is objectively trivial

Run `wade review implementation` **before** calling
`wade implementation-session done` — unless the change meets one of the
may-skip criteria in **Review budget & skip guidance** below, in which case use
`wade implementation-session done --skip-review` and name the criterion (see
below for how).

If you see the review reminder in the `done` output, it means neither a review
ran nor a sanctioned skip was recorded for the current commit — go back, run
the review (or apply a sanctioned skip), address any findings, then present
results to the user.
