const mockDirectoryCreate = jest.fn();
const mockFileCopy = jest.fn(async () => undefined);

jest.mock("expo-file-system", () => {
  class Directory {
    create = mockDirectoryCreate;
    uri: string;

    constructor(root: string, name: string) {
      this.uri = `${root}/${name}`;
    }
  }

  class File {
    copy = mockFileCopy;
    uri: string;

    constructor(root: string | Directory, name?: string) {
      this.uri = typeof root === "string" ? root : `${root.uri}/${name ?? ""}`;
    }
  }

  return {
    Directory,
    File,
    Paths: { document: "file:///app-documents" },
  };
});

import { persistEvidenceFile } from "./durable-evidence-file.native";

it("copies captured proof into app-owned document storage before enqueue", async () => {
  const persisted = await persistEvidenceFile(
    "file:///picker-cache/signature.png",
    "dc0de2b2-e6d8-4d4f-b898-42398bab8eaa",
    "png",
  );

  expect(mockDirectoryCreate).toHaveBeenCalledWith({
    idempotent: true,
    intermediates: true,
  });
  expect(mockFileCopy).toHaveBeenCalledWith(
    expect.objectContaining({
      uri: "file:///app-documents/delivery-evidence/dc0de2b2-e6d8-4d4f-b898-42398bab8eaa.png",
    }),
    { overwrite: true },
  );
  expect(persisted).toBe(
    "file:///app-documents/delivery-evidence/dc0de2b2-e6d8-4d4f-b898-42398bab8eaa.png",
  );
});
