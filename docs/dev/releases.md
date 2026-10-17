# Releases

Merging a PR runs CI but does not create a release version or draft. Choose
the next semantic version when a batch is ready to ship. Conventional Commit
titles remain useful for the changelog; they no longer trigger version bumps.

From a clean, current `main` checkout:

1. Run `./scripts/check-all.sh`.
2. Preview the selected bump with
   `uv run python scripts/auto_version.py patch --dry-run` (or `minor`/`major`).
3. Run `uv run python scripts/auto_version.py patch` with the chosen bump.
   This synchronizes source versions, `uv.lock`, the changelog, the release
   commit, and its annotated tag. Inspect the resulting commit and tag.
4. Push the release commit and that tag explicitly, for example
   `git push --atomic origin HEAD:main refs/tags/v1.4.9`, replacing the example
   tag with the one just created. Do not push every local tag.
5. The tag-triggered `Create Draft Release` workflow prepares notes for this
   deliberate release. Review the draft and publish it in GitHub Releases
   when ready. `Publish to PyPI` then builds and uploads that release.

Existing drafts are retained. Publish an older draft only if that version is
intentionally being shipped. The `pypi` environment and Trusted Publishing
configuration still govern package uploads.

CI keeps linting, strict types, all three Python versions, and Docker E2E
coverage. A newer PR commit cancels superseded CI on that PR; main-branch runs
are allowed to finish.
