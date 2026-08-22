# deploy: the prepared, unapplied shape of the question-answering service

**Nothing in this directory has been applied.** It is the deployment the AI layer (ADR 0006)
would run as, written down so the decision to run it can be made on something concrete, and
tested so the shape cannot drift from the code. Applying it provisions cloud resources that
cost money and expose a public endpoint; that is the owner's call, and it has not been made.

## The shape

One AWS Lambda function (`disclosed.ask.service.lambda_handler`, Python 3.12, arm64) behind a
Lambda Function URL, defined in `template.json` (AWS SAM, in JSON so the test suite can read it
with the standard library).

| Property | Value | Why |
| --- | --- | --- |
| CORS | `AllowOrigins` = the Pages origin only, `POST` only | A page on any other origin gets no CORS headers and the browser drops the reply. The service also checks the `Origin` header itself (`service._cors`), so the two agree. |
| Reserved concurrency | 2 | The hard cost bound that does not depend on the process remembering anything: at most two questions are ever in flight. |
| In-process limits | 20 per client per hour, 400 per day | `DISCLOSED_ASK_PER_CLIENT_PER_HOUR`, `DISCLOSED_ASK_PER_DAY`; counted per container, reset by the clock; stated as a limitation, not a guarantee. |
| Invocations alarm | more than the daily cap in one hour | Someone is turning the dial. |
| Budget | `MonthlyBudgetUsd` (default 25), notify at 80% | Bedrock plus Lambda. The e-mail address is a parameter supplied at apply time and is never committed. |
| IAM | `bedrock:InvokeModel` on the one configured model, nothing else | The function cannot read a bucket, write a log group it did not create, or call another model. |
| Model | `global.anthropic.claude-sonnet-4-6` on Bedrock | The only model this account could invoke on 2026-08-21. The code default is `claude-sonnet-5`; switching is one parameter. |
| Logs | 14-day retention | The service logs nothing a reader typed; the group holds runtime errors only. |
| Memory / timeout | 1024 MB / 60 s | The evidence store builds in about a second from the packaged data; two model calls fit comfortably. |

The package is the installed `disclosed` wheel plus `data/` and `corpus/`, assembled by
`build.sh` into `build/package/`; the template's `CodeUri` points there. The function reads
the committed artifacts from `/var/task` (`DISCLOSED_ROOT`) and builds the evidence store on
the first request of each container.

## What applying it would take

```sh
# Not run. Each line is a decision.
sh deploy/build.sh
sam deploy --template-file deploy/template.json --stack-name disclosed-ask \
  --capabilities CAPABILITY_IAM --resolve-s3 \
  --parameter-overrides BudgetEmail=<owner address> MonthlyBudgetUsd=25
# then rebuild the site with the endpoint the stack outputs:
disclosed site --report data/report.json --national data/national.json \
  --scorecard-census data/scorecard-census.json --out site --generated $(date -u +%F) \
  --ask-endpoint <AskEndpoint output>
```

`pages.yml` does not pass `--ask-endpoint`. Until it does, the published site carries no
script and makes no request to anywhere, exactly as before ADR 0006.

## Decisions this does not make

- **Whether to deploy at all.** A public endpoint with a model behind it is a cost dial that
  other people can turn. The bounds above cap it; they do not make it free.
- **The monthly budget and who is notified.** Parameters, supplied at apply time.
- **The subprocessor record.** A reader's question is sent to the model provider for the
  duration of the request. `docs/RESPONSIBLE-TECH-AUDITS.md` records this as a relationship a
  deployment must document; the document does not exist yet.
- **Which model.** Sonnet 5 is the code's default and the owner's stated choice; it was not
  reachable from this account when the service was built. The evaluation results under
  `evals/results/` say which model every number was measured on.
- **Abuse handling beyond the limits.** There is no allow-list, no CAPTCHA and no key on the
  endpoint; reserved concurrency and the budget are the backstop. Whether that is enough for a
  public page is part of the decision.
- **Per-container limits.** The in-process counters reset when a container is recycled and are
  not shared across the two reserved executions. A durable counter (a table) is a small change
  if the limits turn out to matter more than the budget does.
