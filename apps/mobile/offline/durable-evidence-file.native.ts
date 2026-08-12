import { Directory, File, Paths } from "expo-file-system";

export async function persistEvidenceFile(
  sourceUri: string,
  evidenceId: string,
  extension: string,
): Promise<string> {
  const directory = new Directory(Paths.document, "delivery-evidence");
  directory.create({ idempotent: true, intermediates: true });
  const destination = new File(directory, `${evidenceId}.${extension}`);
  await new File(sourceUri).copy(destination, { overwrite: true });
  return destination.uri;
}
