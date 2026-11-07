# Autobuilder Observatory (local)

## Access outside Codex

Open [http://127.0.0.1:8090](http://127.0.0.1:8090) in any browser on this machine
while the Observatory server is running. Codex is not required.

To start the server independently, run in a terminal:

```bash
cd /home/anugeshtu/IdeaProjects/autobuilder
python3 observatory/server.py
```

Keep the terminal open while using the Observatory. Press Ctrl+C to stop it.
Requires Python 3.10 or later, with no additional packages.

The server accepts connections only from this machine. Access from another device
requires additional setup.

## Server and data sources

The server serves `dist/` and proxies a fixed set of read-only JSON endpoints
from the run on port 8081 and home context on port 8080. Override with
`--port`, `--run-port`, and `--home-port`. It binds only to loopback.

The dashboard refreshes every five seconds. Each source reports errors separately;
an unavailable backend never substitutes example data. Roadmap and Tickets read Autobuilder’s own `.claude-memory/roadmap.md` through
the existing `scripts/next_work.py` parser. The local `/api/snapshot` includes
the development queue, and `/api/tickets/<ID>` provides each ticket’s source
text and line number. These views work without the evolution backend. Ticket
status follows the authoritative marker, preserving distinct closure kinds. Recent run events provide changes, agent activity, decisions,
evaluations, and signal evidence. These views cover the backend's recent event
window, not its full persisted history. Import conflicts show receiving and imported
values. Decisions and model consultations use existing Autobuilder controls;
this local monitor does not mutate projects or initiate LLM calls.

Export downloads the latest live snapshot. The original example implementation
is preserved in `dist/app.js` but is no longer loaded. Browser records from the
hosted site are not copied to this origin. Local changes are not published.

## Development workspace

Overview, Agent roles, Changes, Merge workbench, Decision history, Evaluations,
and Signals now describe Autobuilder itself. `meta.py` reads the parent repository's
Git history and working tree, agent/protocol source paths, an allowlist of design
and process review documents, and saved Surefire XML reports. Document reads use
`/api/documents/<encoded-path>` and reject paths outside the allowlist.

Changes include other sessions' uncommitted work. Commit-to-ticket links are extracted
from commit titles, not inferred provenance. Role-to-ticket suggestions match title
text. The merge view reports Git index conflicts and existing port review packets;
it does not generate or apply resolutions. Test reports can span invocations and
are not proof of the current checkout. Development signals are queue/working-tree
observations, not the loop's runtime signal strengths.

The separate Target run tab retains runtime monitoring. Development tabs work
without the Spring backend. No development changes are committed or published by
the Observatory, and reading evidence does not execute tests or call an LLM.

Validation: `python3 -m unittest discover -s observatory -p 'test_*.py'` and
`node --check observatory/dist/live.js`.
