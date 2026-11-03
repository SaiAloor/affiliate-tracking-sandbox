# Integration health check: Juniper Supply Co.

I compared 1,502 store orders (1,221 from partner traffic) with what the platform received.
7 issues need a fix. Left as is, partners would be overpaid by about $104,115 and underpaid by about $1,043 in commission for the month. 2 more are informational and need no change.

| Issue | Orders | Commission impact | Fix |
| --- | ---: | ---: | --- |
| Amount sent in cents | 158 | +$103,517 | Divide the amount by 100 before sending it, so it's in the currency's main unit. |
| Sale never reached the platform | 79 | -$536 | Add server-to-server reporting from the store's backend, which doesn't depend on the shopper's browser. |
| Click ID lost before checkout | 71 | -$507 | Keep the clickid parameter on every redirect, and save it in a first-party cookie or local storage on the first page the shopper sees. |
| Refunds not reported | 63 | +$320 | Call the reversal endpoint whenever an order is fully or partly refunded. |
| Wrong currency sent | 148 | +$262 | Send the order's own currency code on every conversion. |
| Test orders sent to production | 3 | +$16 | Use the test key while testing, and skip reporting for orders marked as tests. |
| Requests rejected by the API | 5 | $0 | Correct the field named in the error and resend. |
| Tag fires more than once | 29 | $0 | Already handled: the platform dropped the extra copies. Optional cleanup: fire the tag once per order. |
| Sale outside the lookback window | 4 | $0 | Working as designed: these sales fell outside the 30-day lookback window. |

A positive impact means partners would be overpaid; negative means underpaid. Commission is figured at 8% of the sale, converted to US dollars.
