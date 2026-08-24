import { ImageResponse } from "next/og";

export const alt = "TradeFlow ERP — every handoff, one accountable flow";
export const size = { height: 630, width: 1200 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    <div
      style={{
        background: "#f4f0e8",
        color: "#172332",
        display: "flex",
        height: "100%",
        padding: "72px",
        width: "100%",
      }}
    >
      <div
        style={{
          borderLeft: "12px solid #e05a32",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          paddingLeft: "54px",
          width: "100%",
        }}
      >
        <div
          style={{
            display: "flex",
            fontSize: 28,
            fontWeight: 700,
            letterSpacing: "-0.02em",
          }}
        >
          TRADEFLOW / DISTRIBUTION ERP
        </div>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            fontSize: 82,
            fontWeight: 650,
            letterSpacing: "-0.055em",
            lineHeight: 0.95,
          }}
        >
          <span>Every handoff.</span>
          <span style={{ color: "#b64727" }}>One accountable flow.</span>
        </div>
        <div style={{ display: "flex", fontSize: 25 }}>
          Approve → reserve → pick → deliver → settle
        </div>
      </div>
    </div>,
    size,
  );
}
