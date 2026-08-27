import { ImageResponse } from "next/og";

export const alt = "TradeFlow ERP: run distribution as one accountable flow";
export const size = { height: 630, width: 1200 };
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    <div
      style={{
        background: "#f7f9fc",
        color: "#10233f",
        display: "flex",
        height: "100%",
        padding: "72px",
        width: "100%",
      }}
    >
      <div
        style={{
          borderLeft: "12px solid #175cd3",
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
          <span>Run distribution.</span>
          <span style={{ color: "#175cd3" }}>As one accountable flow.</span>
        </div>
        <div style={{ display: "flex", fontSize: 25 }}>
          Approve → reserve → pick → deliver → settle
        </div>
      </div>
    </div>,
    size,
  );
}
