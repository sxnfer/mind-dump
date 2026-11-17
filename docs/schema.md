# Database schema

> The source of truth is `backend/migrations/`. Update this diagram in the same PR as any new migration.

As of migration 002 (M1):

```mermaid
erDiagram
    items ||--o{ item_tags : "has"
    tags  ||--o{ item_tags : "labels"

    items {
        bigint      id PK "identity"
        text        type "note | link"
        text        title "1-200 chars, optional"
        text        url "required for links, max 2048"
        text        body "required for notes, 1-20000 chars"
        text        status "default saved"
        timestamptz created_at "default now()"
    }
    tags {
        bigint id PK "identity"
        text   name UK "1-50 chars"
    }
    item_tags {
        bigint item_id PK, FK "ON DELETE CASCADE"
        bigint tag_id  PK, FK "ON DELETE CASCADE"
    }
    schema_migrations {
        text        version PK "001, 002, ..."
        text        name
        timestamptz applied_at
    }
```

## Rules the database enforces

| Rule | Why |
| --- | --- |
| `type` is `note` or `link` | Unknown types can't sneak in through a bug or a manual `INSERT`. |
| A link has a `url`; a note has a `body` | The API checks this first; the database is the second layer. |
| Length limits on `title`, `url`, `body` | They match the API's 422 limits, so both layers agree. |
| Deleting an item deletes its `item_tags` rows | No dangling links to deleted items (`ON DELETE CASCADE`). |
| `tags.name` is unique | One row per tag name; items share it. |

## Indexes

| Index | Serves |
| --- | --- |
| `items` primary key | `GET /items/{id}` and newest-first paging (`ORDER BY id DESC`, `id < before`) |
| `item_tags` primary key `(item_id, tag_id)` | Tags of one item |
| `item_tags_tag_id_idx` | Items with one tag |

`schema_migrations` is created by the migration runner (`mind_dump.migrate`), not by a migration file.
