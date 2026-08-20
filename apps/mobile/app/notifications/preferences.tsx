import { Text, View } from "react-native";

export default function NotificationPreferences() {
  return (
    <View style={{ flex: 1, padding: 16 }}>
      <Text style={{ fontSize: 20, fontWeight: "600" }}>
        Notification Preferences
      </Text>
      <Text style={{ marginTop: 8, color: "#666" }}>
        Push and inbox preferences will be managed here.
      </Text>
    </View>
  );
}
