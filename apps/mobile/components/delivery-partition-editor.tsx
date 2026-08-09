import type { AssignedDelivery } from "@tradeflow/delivery-dispatch";
import { colors } from "@tradeflow/design-tokens";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

export type DeliveryPartition = {
  accepted_quantity_base: string;
  damaged_quantity_base: string;
  delivery_line_id: string;
  exception_details: Partial<Record<ExceptionOutcome, ExceptionDetail>>;
  identity_partitions: IdentityPartition[];
  refused_quantity_base: string;
  short_missing_quantity_base: string;
  still_undelivered_quantity_base: string;
};

export type ExceptionOutcome =
  "damaged" | "refused" | "short_missing" | "still_undelivered";

export type ExceptionDetail = {
  evidence_ids: string[];
  reason: string;
  responsible_party_type: "carrier" | "customer" | "staff" | "unknown";
  responsible_subject?: string | null;
};

export type IdentityPartition = {
  accepted_quantity_base: string;
  damaged_quantity_base: string;
  delivery_line_identity_allocation_id: string;
  refused_quantity_base: string;
  short_missing_quantity_base: string;
  still_undelivered_quantity_base: string;
};

export const outcomes = [
  ["accepted_quantity_base", "Accepted"],
  ["refused_quantity_base", "Refused"],
  ["damaged_quantity_base", "Damaged"],
  ["short_missing_quantity_base", "Short / missing"],
  ["still_undelivered_quantity_base", "Still undelivered"],
] as const;

export const exceptionOutcomes = [
  ["refused", "refused_quantity_base", "Refused"],
  ["damaged", "damaged_quantity_base", "Damaged"],
  ["short_missing", "short_missing_quantity_base", "Short / missing"],
  ["still_undelivered", "still_undelivered_quantity_base", "Still undelivered"],
] as const;

export function createInitialPartitions(
  delivery: AssignedDelivery,
): DeliveryPartition[] {
  return delivery.lines.map((line) => ({
    accepted_quantity_base: line.quantityBase,
    damaged_quantity_base: "0",
    delivery_line_id: line.deliveryLineId,
    exception_details: {},
    identity_partitions: line.identityPositions.map((position) => ({
      accepted_quantity_base: position.quantityBase,
      damaged_quantity_base: "0",
      delivery_line_identity_allocation_id:
        position.deliveryLineIdentityAllocationId,
      refused_quantity_base: "0",
      short_missing_quantity_base: "0",
      still_undelivered_quantity_base: "0",
    })),
    refused_quantity_base: "0",
    short_missing_quantity_base: "0",
    still_undelivered_quantity_base: "0",
  }));
}

export function DeliveryPartitionEditor({
  delivery,
  onChange,
  value,
}: {
  delivery: AssignedDelivery;
  onChange: (value: DeliveryPartition[]) => void;
  value: DeliveryPartition[];
}) {
  const change = (
    lineId: string,
    outcome: (typeof outcomes)[number][0],
    quantity: string,
  ) =>
    onChange(
      value.map((line) =>
        line.delivery_line_id === lineId
          ? { ...line, [outcome]: quantity }
          : line,
      ),
    );

  const changeIdentity = (
    lineId: string,
    allocationId: string,
    outcome: (typeof outcomes)[number][0],
    quantity: string,
  ) =>
    onChange(
      value.map((line) => {
        if (line.delivery_line_id !== lineId) return line;
        const identityPartitions = line.identity_partitions.map((candidate) =>
          candidate.delivery_line_identity_allocation_id === allocationId
            ? { ...candidate, [outcome]: quantity }
            : candidate,
        );
        return {
          ...line,
          ...Object.fromEntries(
            outcomes.map(([field]) => [
              field,
              sumQuantities(identityPartitions.map((item) => item[field])),
            ]),
          ),
          identity_partitions: identityPartitions,
        };
      }),
    );

  return (
    <View accessibilityLabel="Delivery outcome partition" style={styles.wrap}>
      <Text style={styles.eyebrow}>QUANTITY OUTCOME</Text>
      <Text style={styles.intro}>
        Classify every dispatched unit. Only accepted quantity leaves company
        custody.
      </Text>
      {delivery.lines.map((deliveryLine) => {
        const partition = value.find(
          (line) => line.delivery_line_id === deliveryLine.deliveryLineId,
        );
        if (partition === undefined) return null;
        const balance = partitionReadiness(partition, deliveryLine);
        return (
          <View key={deliveryLine.deliveryLineId} style={styles.line}>
            <View style={styles.lineHeading}>
              <View>
                <Text accessibilityRole="header" style={styles.sku}>
                  {deliveryLine.skuName}
                </Text>
                <Text style={styles.meta}>{deliveryLine.skuCode}</Text>
              </View>
              <Text style={styles.dispatched}>
                {deliveryLine.quantityBase} dispatched
              </Text>
            </View>
            <Pressable
              accessibilityRole="button"
              onPress={() =>
                onChange(
                  value.map((line) =>
                    line.delivery_line_id === deliveryLine.deliveryLineId
                      ? {
                          ...line,
                          accepted_quantity_base: deliveryLine.quantityBase,
                          damaged_quantity_base: "0",
                          refused_quantity_base: "0",
                          short_missing_quantity_base: "0",
                          still_undelivered_quantity_base: "0",
                          identity_partitions: line.identity_partitions.map(
                            (identity, index) => ({
                              ...identity,
                              accepted_quantity_base:
                                deliveryLine.identityPositions[index]
                                  ?.quantityBase ??
                                identity.accepted_quantity_base,
                              damaged_quantity_base: "0",
                              refused_quantity_base: "0",
                              short_missing_quantity_base: "0",
                              still_undelivered_quantity_base: "0",
                            }),
                          ),
                        }
                      : line,
                  ),
                )
              }
              style={styles.allAccepted}
            >
              <Text style={styles.allAcceptedText}>MARK ALL ACCEPTED</Text>
            </Pressable>
            <View style={styles.fields}>
              {outcomes.map(([field, label]) => (
                <View key={field} style={styles.field}>
                  <Text style={styles.label}>{label}</Text>
                  {deliveryLine.identityPositions.length === 0 ? (
                    <TextInput
                      accessibilityLabel={`${deliveryLine.skuName} ${label} quantity`}
                      inputMode="decimal"
                      onChangeText={(quantity) =>
                        change(deliveryLine.deliveryLineId, field, quantity)
                      }
                      style={styles.input}
                      value={partition[field]}
                    />
                  ) : (
                    <Text style={styles.derived}>{partition[field]}</Text>
                  )}
                </View>
              ))}
            </View>
            {deliveryLine.identityPositions.map((position) => {
              const identity = partition.identity_partitions.find(
                (value) =>
                  value.delivery_line_identity_allocation_id ===
                  position.deliveryLineIdentityAllocationId,
              );
              if (identity === undefined) return null;
              return (
                <View
                  key={position.deliveryLineIdentityAllocationId}
                  style={styles.identity}
                >
                  <Text style={styles.identityTitle}>
                    {position.kind === "serial"
                      ? `SERIAL ${position.serialNumber ?? ""}`
                      : `LOT ${position.lotCode ?? ""} · ${position.quantityBase}`}
                  </Text>
                  <View style={styles.fields}>
                    {outcomes.map(([field, label]) => (
                      <View key={field} style={styles.field}>
                        <Text style={styles.label}>{label}</Text>
                        <TextInput
                          accessibilityLabel={`${position.kind === "serial" ? `Serial ${position.serialNumber ?? ""}` : `Lot ${position.lotCode ?? ""}`} ${label} quantity`}
                          inputMode="decimal"
                          onChangeText={(quantity) =>
                            changeIdentity(
                              deliveryLine.deliveryLineId,
                              position.deliveryLineIdentityAllocationId,
                              field,
                              quantity,
                            )
                          }
                          style={styles.input}
                          value={identity[field]}
                        />
                      </View>
                    ))}
                  </View>
                </View>
              );
            })}
            <Text
              accessibilityLiveRegion="polite"
              style={balance.valid ? styles.balanced : styles.unbalanced}
            >
              {balance.valid ? "BALANCED · READY TO SAVE" : balance.message}
            </Text>
          </View>
        );
      })}
    </View>
  );
}

export function partitionsAreValid(
  delivery: AssignedDelivery,
  partitions: DeliveryPartition[],
): boolean {
  return (
    partitions.length === delivery.lines.length &&
    delivery.lines.every((line) => {
      const partition = partitions.find(
        (candidate) => candidate.delivery_line_id === line.deliveryLineId,
      );
      return (
        partition !== undefined && partitionReadiness(partition, line).valid
      );
    })
  );
}

function equalQuantity(total: string, parts: string[]): boolean {
  const target = parseQuantity(total);
  const values = parts.map(parseQuantity);
  return (
    target !== null &&
    values.every((value) => value !== null) &&
    values.reduce<bigint>((sum, value) => sum + (value ?? 0n), 0n) === target
  );
}

function partitionReadiness(
  partition: DeliveryPartition,
  line: AssignedDelivery["lines"][number],
): { message: string; valid: boolean } {
  const aggregate = partitionBalance(partition, line.quantityBase);
  if (!aggregate.valid) return aggregate;
  if (partition.identity_partitions.length !== line.identityPositions.length)
    return { message: "TRACKED POSITIONS CHANGED · REFRESH", valid: false };
  for (const position of line.identityPositions) {
    const identity = partition.identity_partitions.find(
      (candidate) =>
        candidate.delivery_line_identity_allocation_id ===
        position.deliveryLineIdentityAllocationId,
    );
    if (identity === undefined)
      return { message: "TRACKED POSITIONS CHANGED · REFRESH", valid: false };
    const balance = partitionBalance(identity, position.quantityBase);
    if (!balance.valid)
      return { message: "BALANCE EVERY TRACKED POSITION", valid: false };
    if (position.kind === "serial") {
      const values = outcomes.map(([field]) => parseQuantity(identity[field]));
      const one = parseQuantity(position.quantityBase);
      if (
        one !== 1_000_000n ||
        values.filter((value) => value === one).length !== 1 ||
        values.some((value) => value !== 0n && value !== one)
      )
        return { message: "CHOOSE ONE OUTCOME PER SERIAL", valid: false };
    }
  }
  if (
    line.identityPositions.length > 0 &&
    !outcomes.every(([field]) =>
      equalQuantity(
        partition[field],
        partition.identity_partitions.map((identity) => identity[field]),
      ),
    )
  )
    return { message: "TRACKED TOTALS DO NOT MATCH", valid: false };
  return { message: "", valid: true };
}

function sumQuantities(values: string[]): string {
  const parsed = values.map(parseQuantity);
  if (parsed.some((value) => value === null)) return "";
  const total = parsed.reduce<bigint>((sum, value) => sum + (value ?? 0n), 0n);
  return `${total / 1_000_000n}.${(total % 1_000_000n).toString().padStart(6, "0")}`;
}

export function hasDeliveryException(partitions: DeliveryPartition[]): boolean {
  return partitions.some((line) =>
    [
      line.refused_quantity_base,
      line.damaged_quantity_base,
      line.short_missing_quantity_base,
      line.still_undelivered_quantity_base,
    ].some((quantity) => parseQuantity(quantity) !== 0n),
  );
}

export function acceptsDeliveryQuantity(
  partitions: DeliveryPartition[],
): boolean {
  return partitions.some(
    (line) => (parseQuantity(line.accepted_quantity_base) ?? 0n) > 0n,
  );
}

function partitionBalance(
  partition: DeliveryPartition | IdentityPartition,
  dispatched: string,
): { message: string; valid: boolean } {
  const target = parseQuantity(dispatched);
  const values = outcomes.map(([field]) => parseQuantity(partition[field]));
  if (target === null || values.some((value) => value === null)) {
    return { message: "ENTER NONNEGATIVE QUANTITIES", valid: false };
  }
  const total = values.reduce<bigint>((sum, value) => sum + (value ?? 0n), 0n);
  if (total !== target) {
    return { message: "OUTCOMES MUST EQUAL DISPATCHED", valid: false };
  }
  return { message: "", valid: true };
}

function parseQuantity(value: string): bigint | null {
  const match = /^(0|[1-9]\d*)(?:\.(\d{1,6}))?$/.exec(value);
  if (match === null) return null;
  const whole = match[1] ?? "0";
  const fraction = (match[2] ?? "").padEnd(6, "0");
  return BigInt(whole) * 1_000_000n + BigInt(fraction);
}

const styles = StyleSheet.create({
  allAccepted: { minHeight: 44, paddingVertical: 13 },
  allAcceptedText: { color: colors.orange, fontSize: 11, fontWeight: "700" },
  balanced: { color: colors.green, fontSize: 11, fontWeight: "700" },
  dispatched: { color: colors.inkMuted, fontSize: 12 },
  derived: {
    color: colors.ink,
    fontFamily: "monospace",
    minHeight: 44,
    paddingVertical: 12,
  },
  eyebrow: {
    color: colors.orange,
    fontSize: 11,
    fontWeight: "700",
    letterSpacing: 1.4,
  },
  field: { flexBasis: "46%", flexGrow: 1 },
  fields: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  input: {
    borderBottomColor: colors.ink,
    borderBottomWidth: 1,
    color: colors.ink,
    fontSize: 16,
    minHeight: 44,
    paddingVertical: 8,
  },
  intro: { color: colors.inkMuted, lineHeight: 20, marginTop: 6 },
  identity: {
    borderLeftColor: colors.orange,
    borderLeftWidth: 2,
    marginTop: 16,
    paddingLeft: 12,
  },
  identityTitle: {
    color: colors.ink,
    fontSize: 11,
    fontWeight: "700",
    marginBottom: 8,
  },
  label: { color: colors.inkMuted, fontSize: 11, textTransform: "uppercase" },
  line: {
    borderTopColor: colors.paperDeep,
    borderTopWidth: 1,
    paddingVertical: 18,
  },
  lineHeading: { flexDirection: "row", justifyContent: "space-between" },
  meta: { color: colors.inkMuted, fontSize: 11, marginTop: 2 },
  sku: { color: colors.ink, fontSize: 17, fontWeight: "700" },
  unbalanced: { color: colors.red, fontSize: 11, fontWeight: "700" },
  wrap: { marginTop: 20 },
});
