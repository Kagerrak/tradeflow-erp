# TradeFlow ERP cross-platform strategy

## Shared capabilities

Web and mobile share:

- OpenAPI-generated API types and client;
- authentication/session contracts;
- business terminology and validation messages;
- formatting rules for money, quantities, dates, and document numbers;
- design tokens and icon vocabulary where practical;
- event names, telemetry fields, and error codes.

Business rules stay on the server. Client validation improves usability but
does not authorize or post business transactions.

## Web console

The web application is optimized for:

- dense searchable tables and bulk actions;
- customer and product master data;
- sales/purchase document entry;
- approval queues;
- receivables, statements, expenses, and commission review;
- reporting, configuration, imports, and audit investigation.

Use responsive layouts, but do not force mobile field tasks into desktop table
patterns.

## Android/iOS application

The Expo application is optimized for:

- customer lookup and sales visit context;
- quick sales-order draft capture;
- barcode-assisted receiving, picking, and inventory lookup;
- delivery route, recipient, signature/photo, and exception capture;
- return/damage photos and inspection notes;
- expense receipt capture;
- assigned approvals and operational notifications.

Use camera, barcode scanning, secure storage, deep links, push notifications,
background upload, and SQLite where they materially improve these workflows.

## Offline policy

Phase one permits cached authorized reference data, offline Sales Order drafts,
and offline Proof of Delivery evidence. The mobile client durably stores each
draft with a client-generated identity and idempotent outbox command, presents
it as Pending Sync, and resumes interrupted evidence uploads.

Commercial Approval, Inventory Reservation, Pick or Dispatch posting, Delivery
Confirmation, stock movement, and invoice creation require server
acknowledgement. If server state changed while the device was offline, TradeFlow
does not auto-merge a posting conflict; it routes the conflict for explicit
review. Broader offline posting requires a later policy decision.

## Release model

- separate development, preview, and production environments;
- EAS build profiles for internal and store distribution;
- runtime versions preventing incompatible OTA updates;
- staged rollout, crash monitoring, and rollback;
- web preview deployments per change;
- API compatibility window so older supported mobile clients remain safe.

## UX consistency

Consistency means shared concepts and outcomes, not identical layouts. A sales
order has the same status and totals everywhere; web may use a multi-column
editor while mobile uses guided steps.
