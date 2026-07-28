# Finance domain language

**Transaction Currency**: The single currency in which a commercial document's
amounts are stated.

**Exchange Rate Snapshot**: The approved rate, rate date, source, Transaction
Currency amount, and Base Currency amount retained by a foreign-currency
posting.

**Foreign Exchange Adjustment**: An immutable posted difference caused by
settling a foreign-currency obligation at a different rate. It does not rewrite
the historical posting.

**Money Amount**: A decimal value in one Currency rounded to that currency's
minor unit using round-half-up for posting.

**Invoice**: A posted customer charge arising from delivered goods or approved
billable activity.

**Draft Invoice**: An unposted charge created idempotently from one confirmed
Delivery's accepted quantities. It has no customer-ledger effect until Finance
approves and posts it.

**Invoice Balance**: Posted invoice amount minus allocated payments and credits.

**Payment Receipt**: A recorded receipt of customer funds. It does not reduce a
specific invoice until allocated. Non-cash receipts begin Pending Verification
unless confirmed by an approved provider.

**Cleared Payment**: A Payment Receipt whose funds have satisfied the configured
verification and approval requirements for its Payment Method.

**Pending Verification**: A Payment Receipt state awaiting an eligible Finance
User's verification of evidence, value date, account or provider, and external
reference.

**Payment Verification**: Maker-checker confirmation by an eligible Finance
User other than the recorder that a non-cash Payment Receipt has cleared.

**External Payment Reference**: A provider, bank, or check reference that must
be unique among active receipts for one Company and Payment Method.

**Customer Prepayment**: A Cleared Payment retained as unapplied value until the
related Delivery's Invoice posts.

**COD Payment Receipt**: A Payment Receipt captured for the accepted quantities
of a Cash on Delivery shipment. It remains unapplied until the related Invoice
posts.

**Cash Reconciliation**: The controlled comparison and settlement of physical
cash collected by an authorized delivery user against COD Payment Receipts.

**Payment Method**: The channel used to receive funds, such as cash, bank
transfer, check, card, or e-wallet. It is distinct from Payment Timing Policy.

**Payment Allocation**: The application of payment value to one or more open
invoices.

**Unapplied Payment**: Received value not yet allocated to an invoice.

**Withholding Tax Credit**: Payment-time value supported by a withholding
certificate or reference that satisfies part of a customer receivable. It does
not reduce the originating Sales Order total.

**Credit Note**: A posted reduction of customer receivables, typically arising
from a return, pricing correction, or approved adjustment.

**Debit Adjustment**: A posted increase of customer receivables outside a
standard sales invoice.

**Customer Ledger Entry**: An immutable posted charge, payment allocation,
credit, or adjustment contributing to customer receivables.

**Statement of Account**: A time-bounded projection of customer ledger entries,
open documents, aging, payments, credits, and closing balance. It is not an
editable financial document.

**Open Balance**: The sum of outstanding posted customer ledger value at a
specified time.

**Expense**: A business cost submitted, approved, posted, and categorized with
supporting evidence.

**Expense Claim**: A request for reimbursement. It is not a posted expense until
approved under the configured policy.
