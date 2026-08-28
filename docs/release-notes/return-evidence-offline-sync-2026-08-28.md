# Return Evidence Capture and Offline Sync

TradeFlow now captures photos and notes as evidence on a Return Request and tracks
offline sync state so field staff can record proof without a live connection.

Evidence is capability-scoped and Branch/Warehouse-scoped. Photos use resumable
multipart uploads with SHA-256 verification. Notes are stored inline and verified
immediately. The mobile SQLite outbox holds pending evidence and replays it when
connectivity returns; if the Return Request was authorized while the device was
offline, the sync is rejected with an explicit conflict that can be reviewed.

The responsive web return-authorization workspace shows captured evidence and
sync status. The generated API client includes the new evidence, upload, and
offline-sync schemas.

Replacement fulfillment, Credit Requests, Return Receipt inspection, and
Disposition remain assigned to later Returns slices.
