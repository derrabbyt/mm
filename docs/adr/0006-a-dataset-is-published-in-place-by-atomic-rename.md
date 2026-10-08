---
status: accepted
---

# A Dataset is published in place, by renaming the manifest

The bake writes into the same directory the API is reading. It writes each
matrix beside its final name and renames it on top, assembles the manifest,
reads the finished Dataset back through the reader's own model, runs both
publication checks — and only then renames `manifest.json` into place. That
rename is the publication.

Nothing else changes. No staging directory, no symlink, no object storage, and
the directory layout the reader already knows stays exactly as it is.

It works because of two properties that were already true. Matrix filenames are
content-addressed, so a bake with new inputs writes new names that the live
manifest does not mention; and a memory mapping follows the inode, not the name,
so a reader holding the previous files keeps reading them after a rename puts
different ones there. The manifest is the only file both sides contend over, and
`os.replace` of it is atomic.

What this replaces: `np.save` straight to the final path, and
`manifest.json` written with `write_text`, into a directory mounted read-write
into the bake container and read-only into the API's. A re-bake whose inputs had
not changed overwrote the exact inode a live API had mapped, which is a `SIGBUS`
in the API on the next page it touches. A kill mid-write left a truncated `.npy`
at the name the next run would reuse, and `np.load(mmap_mode="r")` reads only the
header, so the shape check passed and the API took the fault later.

## Considered Options

**A staging directory, then move the finished folder into place.** The obvious
one, and it cannot be done atomically: `os.replace` onto a non-empty directory
fails, so "move the folder" is really "delete the old one and rename", with a
window in which there is no Dataset at all. It also doubles the disk requirement
for 1.4 GB of matrices and needs a second configured path.

**A symlink that the reader follows, swapped with `os.replace`.** Atomic, and
genuinely the standard answer. Rejected because it buys nothing here: the API
opens the Dataset once, at import, so it is not re-reading the symlink anyway,
and the thing the symlink would protect — a reader seeing a half-written file —
is already handled by renaming each file into place. It would add a level of
indirection to the one path everything in the system is configured with.

**Object storage, with the API downloading and validating a new Dataset.** The
intended eventual home, and out of scope. It is also a different problem:
publication stops being a rename and becomes a transfer, and the reader needs
the hot-swap it does not have yet.

**Write a new versioned sibling folder per bake and point the setting at it.**
Rejected: it makes publication a deployment change rather than something the job
finishes, and a weekly job would accumulate 1.4 GB folders that nothing prunes.
A grid change does still want a new folder, and the bake refuses to write a
different grid into an existing one.

## Consequences

**A bad bake cannot take the API down, and cannot degrade it while it runs.**
Three hours of baking happen next to a Dataset that stays complete and readable
throughout, and a Dataset that fails validation or either check is never
published at all — the previous `manifest.json` is still the one on disk.

**A published Dataset needs a restart to be served.** The reader builds its
`Dataset` at import. That was already true and is not made worse; the atomic
publish is the prerequisite for ever doing better.

**Superseded matrix files stay in the folder.** They have to: a reader that has
not reopened is still mapping them. The bake names them in its log and deletes
nothing, so reclaiming the disk is an operator's decision. Left alone, a folder
grows by up to 1.4 GB per bake whose inputs changed.

**A bake that is interrupted resumes.** Filenames being content-addressed, a
matrix already on disk under its build hash was built from these inputs, and the
next run reuses it rather than spending its share of three hours again. This is
safe now in a way it was not before: every matrix is renamed into place, so a
file under its final name is a complete one, and the manifest entry is measured
off the array each time rather than carried down a separate "already exists"
path — which is the path that used to write a manifest the reader rejected.
