# BogPilot source pins

Frozen at Phase 0. Do not float these SHAs mid-port. All ids below were observed with `git ls-remote` and confirmed with `git rev-parse` after clone.

## Source hierarchy

When sources conflict, use this order:

1. Current FrogPilot `FrogPilot` branch behavior for shared framework code (pinned below).
2. `frog_ap1` as a patch index only: how a May 2025 FrogPilot tree was bent for AP1. Re-diff against today's FrogPilot. Do not git-merge it.
3. earlytesla-openpilot / Tinkla for Tesla-only behavior (stalk map, IC messages, pre-AP vs AP1 vs AP2 control split).
4. Bus captures and opendbc the human supplies.
5. Third-party Pre-AP ports only for stale signal-name resolution (never as a behavior source). Disagreements go in `docs/tesla/DIVERGENCES.md` later. Do not replace the Tinkla feature set with an outside fork.

## 1. Base — FrogPilot

| Field | Value |
| --- | --- |
| URL | https://github.com/FrogAi/FrogPilot |
| Branch | `FrogPilot` (not Staging, Testing, Development, or Development-New) |
| SHA | `1e23dec6352cef5a36a87be0af7d7a082b7c48a4` |
| Date | 2026-07-04 15:00 ET (commit `2026-07-04T12:00:00-07:00`) |
| Subject | `Compile FrogPilot` |
| Author | James |
| Clone | `/workspace/inventory/FrogPilot` (`--depth 1`, then `--unshallow` on this branch) |
| Install channel string in tree | still `frogpilot.download` in older README lineage; rebrand is Phase 1, not done here |

`refs/heads/FrogPilot-Development` does not exist. Other heads seen while pinning: `FrogPilot-Staging` `6976ff3db25adbf309a30f743a63bec24b2806f5`, `FrogPilot-Testing` `728f654727be9ecb71c16d95bd869ed051bb147f`, `FrogPilot-Development-New` `9b4d7ccc785f8e0812c1bb343a509c33e55bcebb`. None of those are the base.

The `FrogPilot` branch was rewritten. After unshallow it has exactly six commits, all dated 2026-07-04 15:00 ET:

| SHA | Subject |
| --- | --- |
| `1e23dec6352cef5a36a87be0af7d7a082b7c48a4` | Compile FrogPilot (HEAD) |
| `78f1666d3e83ee0bd6e383c5a119b6263bac4276` | Rewrite |
| `1b83cdda92dd6953304f9f68af30a85c6dfc3576` | July 4th, 2026 Update |
| `793971ab0148cd52e306a5debfcd98570179fd30` | FrogPilot 0.9.7 |
| `21d0056f4ee26850074dad65441053e8ddd8696d` | OPGM v0.9.7 release |
| `e05eeaaaa0783401eab433cb0d678dd9f598ebe7` | openpilot v0.9.7 release (root, no parents) |

The root commit message embeds `master commit: f8cb04e4a8b032b72a909f68b808a50936184bee` and `date: 2024-06-11T01:36:39`. That note is not a parent SHA. `opendbc`, `panda`, and `cereal` are vendored trees, not gitlinks.

## 2. Tesla behavior — earlytesla-openpilot

| Field | Value |
| --- | --- |
| URL | https://github.com/bbqgloves/earlytesla-openpilot |
| Branch | `tesla_unity_dev` (only remote head; no fallback needed) |
| SHA | `501c7de91b59c70510e9dc1585acfde9b7102c93` |
| Date | 2023-03-23 16:57 ET |
| Subject | `bump panda` |
| Author | BogGyver |
| Clone | `/workspace/inventory/earlytesla-openpilot` (`--depth 1`) |

Submodules are not checked out inside that clone (relative gitlinks). They were cloned separately at the gitlink SHAs, which are also `HEAD` of `https://github.com/BogGyver/{opendbc,panda}.git` on branch `tesla_unity_dev`:

| Submodule | Gitlink / HEAD | Date (ET) | Subject | Clone |
| --- | --- | --- | --- | --- |
| opendbc | `9c0b6fe3ae205beccb879635750150620b8f7f9b` | 2022-10-30 00:59 | `fix CANParser definition` | `/workspace/inventory/earlytesla-opendbc` |
| panda | `f7751e4cf54de1719ba7ba7381c76cdde5328bb7` | 2023-03-23 16:56 | `send 0x659 only when no AP hardware` | `/workspace/inventory/earlytesla-panda` |
| cereal | `f8f68fdb62a8c94762f6081a2253d43fe5b5b45a` | not cloned | BogGyver/cereal `HEAD` matches the gitlink | not needed for path inventory |

`commaai/opendbc` and `commaai/panda` HEADs are different commits. Do not substitute them.

## 3. Bridge — frog_ap1

| Field | Value |
| --- | --- |
| URL | https://github.com/bbqgloves/openpilot |
| Branch | `frog_ap1` (also present, not used: `frog-ap1-new`, `frog-ap1-old`, `frogtesla_dev`) |
| SHA | `73a16cdd50033cb9f57a03fd619e2129558eef43` |
| Date | 2025-05-20 05:31 ET (`2025-05-20T11:31:43+02:00`) |
| Subject | `fix` |
| Author | (redacted — third-party; do not cite) |
| Clone | `/workspace/inventory/frog_ap1` (started `--depth 1`, deepened ~40 commits; not a full history) |

### Parent / divergence

Current `FrogPilot` and `frog_ap1` **do not share commits**.

- `FrogPilot` root `e05eeaaaa0783401eab433cb0d678dd9f598ebe7` is not an ancestor of `frog_ap1` HEAD in the fetched graph, and that root object is dated 2026-07-04, after `frog_ap1` HEAD.
- Immediate parent of `frog_ap1` HEAD is `8532c208c72761350fc58c2b8053a53f1e690b45` (`fix`, 2025-05-19 15:15 ET). That commit is still on the bridge line. `git fetch` of that SHA from `FrogAi/FrogPilot` succeeds (object still reachable from some non-`FrogPilot` ref, likely a PR), but it is **not** in the six-commit `FrogPilot` branch history. Do not treat it as the FrogPilot base.
- README.md on `frog_ap1` does **not** pin a FrogPilot SHA. It still advertises install URL `frogpilot.download` and tells users not to install `FrogPilot-Development`.

Historical cut, from `frog_ap1` first-parent (this is the FrogPilot snapshot the AP1 commits were applied to, before the 2026 rewrite):

| SHA | Role |
| --- | --- |
| `f9bebfc4dbab64b1d31d72a752ed1ce88cbfa51a` | `May 7th, 2025 Update` by FrogAi, 2025-05-07 17:44 ET. Last plain FrogPilot snapshot under the AP1 work. |
| `c1c9eecdd7d79e3cbe13f9d0cfb9893638a98c75` | `Merge pull request #252 from chrispypatt/frogpilot-pr` by James, parents `f9bebfc4` and `f26103a528b2558e124c992c4763a8986a7805c9`. |
| `b6eea897a3154fdbf8f58d1f018a638046eb08cf` | First AP1-specific commit: `remove unnecessary overhead for Tesla AP1 support` (2025-05-14 08:44 ET). Oldest unique AP1 commit observed. |

`git diff --stat c1c9eec..73a16cd` touches 8 files, net deletion: tesla car package, `panda/board/safety/safety_tesla.h`, `launch_env.sh` (`FINGERPRINT=TESLA_AP1_MODELS`), and removal of the custom boot-logo copy in `selfdrive/frogpilot/frogpilot_functions.py`. The bridge is a thinning of upstream Tesla Model S support, not a Tinkla import.

Full `frog_ap1` root was not enumerated (shallow). Not required: the rewritten FrogPilot branch has no merge-base with this line.

## Layout note for later phases

May 2025 FrogPilot kept extensions at `selfdrive/frogpilot/`. Pinned July 2026 FrogPilot moved them to top-level `frogpilot/`. Destination paths in `PORT_PLAN.md` follow July 2026.
