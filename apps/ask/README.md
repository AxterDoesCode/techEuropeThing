# Ask about London areas

A standalone web client for the platform's chat assistant (`POST /api/chat`, see `docs/API.md`).
The user asks about an area of London, a walk between two places, or hotels near a place. The
model and its tools run on the server; this application sends the conversation, renders the
answer, and draws the context of the answer on a map.

- Conversation column and context map side by side; on narrow screens the map is a collapsible
  panel above the input.
- The answer text is rendered without a markdown library: paragraphs, list lines (`* `, `- `,
  `•`), `**bold**` and bare http(s) URLs. Everything else is rendered as text.
- "How this was answered" lists the `tool_calls` of the response in plain language and the
  sources as links. Tool call strings that do not match a known format are ignored.
- The map shows a marker per found place, a circle per `area_report` call, the hotel search
  area and first results per `hotels_near` call, and for `walking_route` both routes from
  `POST /api/route` (lower-risk route prominent, shortest route thin grey). When the chat
  response contains `ui.route` (the body of `/api/route`), that is drawn and no route request is
  made. Selecting an earlier answer draws its context again.
- The server is stateless: every request carries the conversation (at most the most recent 30
  messages, starting with a user message). The conversation is kept in `sessionStorage`.
- Errors are shown as inline messages with the API's `detail`. 429 shows an estimated retry
  time; 502, 503, network errors and stopped requests offer a Retry button.

## Run

```
npm install
npm run dev
```

## Environment variables

Set in `.env.local` or in the shell when running `npm run dev` or `npm run build`.

| Variable | Default |
| --- | --- |
| `VITE_CHAT_BASE` | `https://alexchau256--london-risk-chat.modal.run` |
| `VITE_API_BASE` | `https://alexchau256--london-risk-store-api.modal.run` |

The chat service allows 20 questions per 10 minutes per network address.

## Build and test

```
npm run build     # type check, then static files in dist/
npm run preview   # serves dist/
npm test          # vitest: answer parsing, tool call parsing, API response normalisation
```

`dist/` is a static site and can be served from any static host.

## Source layout

- `src/api.ts` — typed client for both services; normalises responses and ignores unknown fields.
- `src/toolCalls.ts` — parser for the `tool_calls` strings and their plain-language descriptions.
- `src/answerFormat.ts` — parser for the answer text.
- `src/conversation.ts` — conversation items, `sessionStorage`, retry time estimate for 429.
- `src/mapContext.ts`, `src/useContextData.ts` — what the map draws for one response, and the
  route and hotel requests for it.
- `src/components/` — React components. `src/fixtures/` — recorded responses used by the tests.
