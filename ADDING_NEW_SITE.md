# Adding a New Site — Instructions for AI Chat Designers

**Audience**: An AI assistant (Claude, ChatGPT, etc.) that the user has come
to in order to add support for a new Dutch municipality booking site.

**Goal**: Produce a working YAML file in `sites/<site_id>.yaml` that the
existing `checker.py` engine can drive without code changes.

---

## Project context

This is `afspraak-watcher` — a GitHub Actions cron that scrapes municipal
appointment booking sites every 10 minutes and notifies the user via
Telegram when an earlier slot becomes available.

The user controls everything from Telegram (`/watch`, `/booked`, `/deadline`,
etc.). **Your job is only to define the site, not to wire up notifications
or thresholds — those are runtime concerns.**

---

## What the user will give you

Typically: a URL of a Dutch municipal booking page and a description of what
they're booking (e.g., "driver's license at Rotterdam municipality").

If they haven't, ask for:
1. The starting URL (the page where the booking flow begins).
2. What action they need to book (so the `name` field is meaningful).

---

## YAML schema reference

```yaml
name: "Display name shown in Telegram messages"
url:  "https://...starting URL..."
max_retries: 3                  # optional, default 3

# Browser navigation: list of actions, run in order
steps:
  - action: goto                # always start with this
    wait_until: domcontentloaded
    timeout: 60000              # ms

  - action: wait                # fixed pause
    duration: 2000              # ms

  - action: click               # CSS or Playwright text selector
    selector: "button:has-text('Volgende')"
    timeout: 15000

  - action: wait_load           # wait for network idle
    state: networkidle          # optional, default networkidle

  - action: fill                # text input (rarely needed)
    selector: "input[name='zip']"
    value: "1234AB"

# Date extraction: tried in order, first that returns a Dutch date wins
extract:
  - type: input_dutch_date          # scan all <input> values for Dutch month names
  - type: text_regex_dutch_date     # regex over the page body
  - type: selector_text             # extract from a specific element
    selector: ".datepicker-current"
```

### Action types — full list

| `action` | Required keys | Optional keys | Notes |
|---|---|---|---|
| `goto` | — | `wait_until`, `timeout` | Navigates to the `url` field at top of YAML |
| `click` | `selector` | `timeout` | Waits for selector to be visible, then clicks |
| `wait` | `duration` (ms) | — | Fixed pause |
| `wait_load` | — | `state` (default `networkidle`) | Waits for the page load state |
| `fill` | `selector`, `value` | — | Types into an input |

Selectors use Playwright syntax. Common patterns:
- `button:has-text('Foo')` — button by visible text
- `text=Foo` — any element with that exact text
- `#id` / `.class` / `input[name="x"]` — standard CSS

### Extraction strategies — full list

| `type` | Required keys | Behavior |
|---|---|---|
| `input_dutch_date` | — | Iterates all `<input>` elements, returns first value that parses as a Dutch date |
| `text_regex_dutch_date` | — | Regex `\d{1,2}\s+(januari\|februari\|...)\s+\d{4}` over `body.innerText` |
| `selector_text` | `selector` | Reads `.first.inner_text()` of that selector and parses as Dutch date |

Strategies are tried in order. List multiple as fallbacks. Recommend listing
`input_dutch_date` first (most reliable when present) then
`text_regex_dutch_date` (works on most pages as fallback).

---

## How to figure out the steps for a new site

You probably can't browse the site yourself unless you have a browser tool.
**Walk the user through inspecting their own browser**:

1. Ask them to open the site in Chrome and click through the booking flow
   until they see the date picker.
2. For each click, ask: "What was the visible text on the button?"
3. Note any "Next" / "Continue" labels — usually in Dutch:
   - `Volgende`, `Volgende stap`, `Doorgaan`, `Verder`, `Ga naar stap N`
4. Ask whether the date picker shows the first available date as input text
   or as a calendar grid.
5. Build the steps array accordingly.

If you have a browser tool, navigate the site yourself and inspect the DOM.

### Common JCC-platform sites (`*.mijnafspraakmaken.nl`)

Many Dutch municipalities use the same JCC backend. Sites at
`<gemeente>.mijnafspraakmaken.nl` typically have an identical 3-step flow:

```yaml
steps:
  - action: goto
    wait_until: domcontentloaded
    timeout: 60000
  - action: wait
    duration: 2000
  - action: click
    selector: "button:has-text('Ga naar stap 2')"
    timeout: 15000
  - action: wait_load
  - action: click
    selector: "button:has-text('Ga naar stap 3')"
    timeout: 15000
  - action: wait_load
  - action: wait
    duration: 3000
extract:
  - type: input_dutch_date
  - type: text_regex_dutch_date
```

So for any new JCC site, you can probably reuse `ridderkerk.yaml` and only
change `name` and `url`. Confirm with the user that the new site has the
same "Ga naar stap N" buttons before assuming.

### Non-JCC sites

If the site uses a different platform (Topicus, iBabs, custom, etc.), the
button text and DOM structure will differ. Inspect the site to find the
right selectors. The framework is generic enough — you only need to map the
user's clicks into `click` actions.

---

## Naming conventions

- `site_id` = the YAML filename without extension. Keep it short and
  lowercase: `ridderkerk`, `rotterdam`, `denhaag`, `denhaag_paspoort`.
- If one municipality has multiple bookable services, suffix the activity:
  `rotterdam_rijbewijs`, `rotterdam_paspoort`.
- `name` (inside YAML) is the human-friendly version shown in Telegram.

---

## After creating the YAML

1. Commit it to `sites/<site_id>.yaml`.
2. Tell the user: **"Now message your Telegram bot: `/watch <site_id>`"**
3. They will receive the first baseline notification within ~10 minutes.

**Do NOT add a deadline, message template, or active flag in the YAML** —
those are user-settable from Telegram. The YAML must remain a pure site
definition. (This separation is what makes the system clean.)

---

## Testing your YAML before committing

If you can run code, do this dry run:

```bash
TELEGRAM_BOT_TOKEN=fake TELEGRAM_CHAT_ID=fake \
  python -c "
from sites import load_site
from state import SiteState, save_site_state
import asyncio
from checker import check_and_notify

# Force the site active for testing
cfg = load_site('your_site_id')
save_site_state('your_site_id', SiteState(active=True))
asyncio.run(check_and_notify(cfg))
"
```

The log should end with `earliest available: YYYY-MM-DD`. The
`send_message` will print an error (fake token) but the scrape worked if a
date was extracted.

If no date is extracted, inspect the page rendering with Playwright's
`headless=False` flag and update the selectors.

---

## Reference: existing site

See `sites/ridderkerk.yaml` for a working example. Treat it as the canonical
template for new JCC sites.
