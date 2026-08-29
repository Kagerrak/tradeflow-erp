"use client";

import { useState } from "react";

import { ReturnAuthorizationWorkspace } from "../../components/return-authorization-workspace";
import { ReturnReceiptWorkspace } from "../../components/return-receipt-workspace";

export default function ReturnsPage() {
  const [tab, setTab] = useState<"authorizations" | "receipts">(
    "authorizations",
  );
  return (
    <>
      <nav aria-label="Returns">
        <button
          aria-current={tab === "authorizations" ? "page" : undefined}
          onClick={() => setTab("authorizations")}
        >
          Authorizations
        </button>
        <button
          aria-current={tab === "receipts" ? "page" : undefined}
          onClick={() => setTab("receipts")}
        >
          Receipt / Inspection
        </button>
      </nav>
      {tab === "authorizations" && <ReturnAuthorizationWorkspace />}
      {tab === "receipts" && <ReturnReceiptWorkspace />}
    </>
  );
}
