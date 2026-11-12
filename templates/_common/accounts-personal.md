**Personal project.** GitHub, Vercel, Supabase and every other service must be Felipe's personal accounts (GitHub `FelipeReis26`), never the work ones.

- The repo has a local git identity: `Felipe Reis <78143596+FelipeReis26@users.noreply.github.com>`. Check `git config user.email` before every commit; the machine's global identity is the work one.
- The only `gh` login on this machine is the work account, so never use `gh` to create, push or deploy this repo.
- Pushes go through a repo-only deploy key, the same pattern as tessera and the portfolio: ssh alias `github-{{name}}` in `~/.ssh/config`, key `~/.ssh/{{name}}_deploy`, remote `git@github-{{name}}:FelipeReis26/<repo>.git`. Felipe creates the GitHub repo and adds the deploy key himself, signed in to his personal account.
