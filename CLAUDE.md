# Instructions for Claude (any future session)

این فایل برای هر Claude/AI assistant که در آینده با این ریپو کار می‌کنه ‐ شامل خود من در سشن‌های بعدی. حاوی همه context لازمه که بدون پرسیدن از کاربر کار رو شروع کنی.

---

## Project: afspraak-watcher

A GitHub Actions cron that scrapes Dutch municipality booking sites every 10 min and notifies the user via Telegram when an earlier appointment slot becomes available. The user controls everything from Telegram (`/watch`, `/booked`, `/deadline`, etc.) — never via GitHub UI.

Read `README.md` for architecture, `ADDING_NEW_SITE.md` for adding sites, `آپگرید-از-v1.md` if you need historical context.

---

## Git workflow — fully automated, no user clicks

**Setup is already done.** A fine-grained PAT is embedded in `.git/config` of this clone. That means:

```bash
cd /sessions/<session>/mnt/afspraak-watcher    # or wherever the user mounts it
git pull           # works, no auth prompt
git push           # works, no auth prompt
```

The PAT scope is: Contents R/W + Workflows R/W on this repo only. Expires per the user's choice (check `git remote get-url origin` if you need to inspect).

### Standard change-and-deploy flow

```bash
cd /sessions/<session>/mnt/afspraak-watcher

# Make your changes via Read/Write/Edit tools (use the C:\Users\... path)

# Then commit + push via bash (use the /sessions/... path):
git -C /sessions/<session>/mnt/afspraak-watcher add -A
git -C /sessions/<session>/mnt/afspraak-watcher commit -m "describe change"
git -C /sessions/<session>/mnt/afspraak-watcher push
```

That's it. No need to ask the user to do anything in GitHub Desktop or the GitHub web UI.

### Important: line endings

Files on the user's Windows disk have CRLF endings; my Linux sandbox writes LF. To avoid bogus "whole file changed" diffs:

```bash
# Before staging, if you've only made TARGETED edits and the diff looks like
# every line is changed → it's line-ending noise. Restore unchanged files:
git -c core.autocrlf=true checkout <unchanged-file>
```

Use `core.autocrlf=true` for any explicit checkout from this side.

### Important: git index corruption

If a `git add -A` ever loses files mid-operation with `error: bad signature 0x00000000 fatal: index file corrupt`:

```bash
rm -f .git/index
git reset HEAD
git add -A
git commit -m "..."
git push
```

This happens occasionally with the Windows-mounted filesystem; the working tree is fine, only the index needs rebuilding.

#### If the index keeps re-corrupting on the mounted folder

Sometimes — especially when doing `git update-index --refresh` or making small changes to one file — the index repeatedly corrupts on the Windows mount, making it impossible to produce a clean commit. The fix is to do git ops in a temp clone on the Linux filesystem instead:

```bash
# Extract the embedded PAT from the user's existing clone
PAT=$(cd /sessions/<session>/mnt/afspraak-watcher && \
      git remote get-url origin | sed -E 's|.*x-access-token:([^@]+)@.*|\1|')

# Fresh clone into /tmp (Linux fs, no Windows-mount weirdness)
cd /tmp && rm -rf aw-tmp
git clone "https://x-access-token:${PAT}@github.com/NimaPirmoradian/afspraak-watcher.git" aw-tmp
cd aw-tmp

# Apply your changes here using sed/Edit/etc.

git config user.email "piremorad@gmail.com"
git config user.name "Nima Pirmoradian"
git add -A && git commit -m "..." && git push

# Then sync the user's mounted clone so it doesn't diverge
cd /sessions/<session>/mnt/afspraak-watcher
rm -f .git/index .git/index.lock
git reset --hard HEAD
git pull origin main
```

Use this temp-clone path proactively for small, targeted edits — it's much more reliable than fighting the Windows-mount index issues.

### File deletion

Deleting files in the mounted folder requires explicit per-session permission:

```
call mcp__cowork__allow_cowork_file_delete with the file path
```

(Once granted for the folder, subsequent deletes work in that folder for the session.)

---

## Triggering a workflow run from sandbox

`api.github.com` is **blocked** by the sandbox proxy (only `github.com` for git operations is allowed). So you cannot programmatically trigger workflow_dispatch via REST API.

**Workaro