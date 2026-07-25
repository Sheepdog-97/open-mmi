# Branch workflow

Open MMI uses three long-lived Git branches so development, vehicle testing,
and production releases remain separate.

```text
feature/* -> nightly -> beta -> main
```

Changes normally move only from left to right. The exception is a production
hotfix, where the isolated fix commit is copied forward into the branches that
already contain newer work.

## Long-lived branches

| Branch | Purpose | Normal source of changes |
| --- | --- | --- |
| `nightly` | Active integration and development | Feature and fix pull requests |
| `beta` | Vehicle-tested release candidate | Promotion pull requests from `nightly` |
| `main` | Conservative production source | Promotion pull requests from `beta` |

Do not use `beta` or `main` for routine feature development. Keep incomplete
work on a feature branch or on `nightly`, never on a production hotfix branch.

## Normal feature development

Create a focused branch from the latest `nightly`:

```bash
git switch nightly
git pull --ff-only origin nightly
git switch -c feature/my-change
```

Commit and push the feature, then open a pull request with:

```text
base: nightly
head: feature/my-change
```

A feature pull request may be squashed when a single clean commit is useful.
Do not combine unrelated changes only because they are being tested on the same
tablet or vehicle.

## Promoting Nightly to Beta

When the current Nightly commit has green CI and the required hardware or
vehicle testing, open a promotion pull request with:

```text
base: beta
head: nightly
```

Prefer a merge commit for a promotion pull request. This preserves the fact that
the exact Nightly history was promoted and makes later ancestry checks easier.
After that merge, GitHub may report `beta` as one commit ahead of `nightly` even
when there are no file differences. That is the promotion merge commit and is
normal.

Continue new development from `nightly`. Do not merge `beta` back into `nightly`
just to make the ahead count disappear.

## Promoting Beta to Main

After Beta has passed its soak period and release checks, open a promotion pull
request with:

```text
base: main
head: beta
```

Merge only the tested Beta candidate. Record the exact Beta commit that was
qualified, and verify that the promotion pull request still contains that commit
before merging.

## Production hotfixes

When production needs a fix while Nightly contains unfinished work, branch from
`main`, not from `nightly` or `beta`:

```bash
git switch main
git pull --ff-only origin main
git switch -c hotfix/describe-the-fix
```

Keep the hotfix focused and preferably produce one final fix commit. Merge the
hotfix into `main` first. Then copy that exact fix into the newer branches:

```bash
git switch beta
git pull --ff-only origin beta
git cherry-pick <hotfix-commit-sha>
git push origin beta

git switch nightly
git pull --ff-only origin nightly
git cherry-pick <hotfix-commit-sha>
git push origin nightly
```

Use pull requests for the propagation steps when branch protection requires
them: create a small branch from each target, cherry-pick the hotfix there, and
open a pull request into that target branch.

Cherry-picking avoids merging all of `main` into a branch that may contain a
different or unfinished implementation. If the same lines changed, Git pauses
for conflict resolution. Edit the file so it preserves both the production fix
and the receiving branch's newer behaviour, then continue:

```bash
git add <resolved-files>
git cherry-pick --continue
```

Run CI on `main`, `beta`, and `nightly` after propagation. A fix that applies
cleanly is not automatically correct on a newer implementation.

## Beta-only corrections

A correction discovered during Beta testing should be developed on a short-lived
branch created from `beta`, then merged into `beta`. Cherry-pick the resulting
fix into `nightly` so future work retains it. The correction will reach `main`
through the next normal Beta promotion.

If the same problem is already urgent in production, treat it as a production
hotfix from `main` instead of merging the Beta branch backwards.

## Uncommitted or unfinished work

Feature branches are preferred for work in progress. If local changes are still
uncommitted when an urgent hotfix starts, either commit them to the feature
branch or stash them before switching branches:

```bash
git stash push -u -m "WIP feature"
```

Restore them only after returning to the original branch:

```bash
git switch feature/my-change
git stash pop
```

## Git branches versus updater channels

The long-lived Git branches and the managed updater channels use some of the
same words, but they are different mechanisms:

```text
Git branches:      nightly -> beta -> main
Updater channels:  nightly / beta / stable
```

The Nightly updater follows the exact installer-recorded checkout and branch.
The Beta and Stable updater channels currently discover version tags from the
official `main` branch; they do not directly install the Git `beta` branch.
See [`design/v1-update-management/update-source-and-channels.md`](design/v1-update-management/update-source-and-channels.md)
for the channel security and trust model.
