# Government receipt exercise

Set `INSTRUCTOR_PASSWORD` in Render, then open `/instructor`. Use the browser's
HTTP Basic prompt: username `instructor`, password from that environment variable.
The page and both instructor API endpoints require authentication. Missing or
empty passwords deny access. No private key is exposed through HTTP or WebSocket.

`npm run build` builds both HTML entries. The protected `/instructor` route serves
`dist/instructor.html` with `FileResponse`, using the same asset mount as the
student page.

The government signing key is generated when the server process starts. Share
the current public key displayed on the instructor page with students through a
trusted channel. `/government/public-key` also publishes the raw Ed25519 public
key encoded as Base64. Redistribute it after a server restart.

Keep one server worker, matching the existing shared simulated network. Attack
mode and replay history are shared across connections in that process and reset
on restart. Instructor controls change government responses only; existing route
failures and other exercises retain their behavior.

## Student commands

```text
CONNECT 10.0.0.8 443
PAY 25.00 fresh-unpredictable-client-nonce
```

`PAY` accepts a positive amount with up to 12 integer digits and 2 decimal places,
and a single-token nonce up to 128 characters. Use a new random nonce for each
transaction. Government requests have no additional random loss or waiting delay.

Acknowledgments are single-line JSON. `message` is an object with exactly two
string fields, `amount` and `nonce`; `signature` is Base64-encoded Ed25519.

```json
{"message":{"amount":"25.00","nonce":"fresh-client-nonce"},"signature":"<Base64 signature>"}
```

The signature covers only the message object, serialized with keys sorted
(`amount`, then `nonce`), no optional whitespace, literal Unicode, and UTF-8
encoding. JSON field names, quoting, and escaping preserve value boundaries;
never sign a raw concatenation of the two values. Required JSON escaping of
quotes and control characters still applies, but the message is not encoded as
a JSON string inside another JSON string.

Python signing-input reconstruction:

```python
json.dumps(receipt["message"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
```

JavaScript signing-input reconstruction:

```javascript
new TextEncoder().encode(JSON.stringify({amount: receipt.message.amount, nonce: receipt.message.nonce}))
```

Validate the two-field string schema, reconstruct these bytes, and verify the
signature with the trusted government key. Then compare `nonce` and `amount`
with the expected transaction. Amounts use two decimal places. There is no
receipt ID; a fresh client nonce binds each receipt to its payment.

## Instructor modes

| Mode | Delivered response |
| --- | --- |
| FORWARD | Genuine message and signature. |
| FORGE | Keep the genuine amount and nonce, but sign with an independent attacker key. |
| REPLAY | Preceding transaction's genuine receipt, including its old nonce. |

Every valid, delivered payment is acknowledged by the government before
interception. The last genuine receipt is retained even when its response is
forged or replaced by a replay. REPLAY with no preceding receipt sends nothing; it never
substitutes the current receipt. A replay passes signature verification, so
students must also check transaction freshness and binding.

FORGE preserves valid receipt JSON and the expected amount and nonce. It uses a
real Ed25519 signature with normal encoding and length, but from an independent
attacker key. JSON, value, and signature-format checks pass. Verification with
the trusted government key detects the forgery; nonce checks detect otherwise
valid replayed receipts.

`GET /instructor/api/state` reads the controls; `POST /instructor/api/mode` updates
them using JSON with `mode`. Neither endpoint exposes signing keys or a government
signing operation. Existing student commands are unchanged; `PAY` is new.
