# Repo layout

> Read this when: you create a new top-level folder or wonder where a file should go.

```text
mind-dump/
├── app/                    # Xcode project: the native Mac app (SwiftUI)
├── backend/                # API and workers (language from D1)
│   ├── src/
│   └── tests/
├── infra/                  # Terraform for your Azure demo (M7)
├── docs/
│   ├── plan/               # the plan, split by topic (read on demand)
│   │   └── milestones/     # one file per milestone
│   └── decisions/          # one record per decision
├── .github/workflows/      # CI (M0)
├── docker-compose.yml      # one command to run the backend (M1)
├── .env.example            # settings names, no real values
├── .gitignore              # includes .env
├── LICENSE                 # from D17
├── CLAUDE.md               # short entry point for agents
├── README.md
└── CONTRIBUTING.md         # how others build and test it (M9)
```
