/**
 * The object storage configuration THIS process resolved, from the rule in
 * storageConfig.ts applied to the environment.
 *
 * Resolved at import rather than on first use, so a deployment that names a
 * backend without giving it credentials dies at boot with a message naming the
 * variable — not at the first upload, hours later, with a stack trace from
 * inside the AWS SDK.
 */
import "dotenv/config";
import { resolveStorage, type StorageConfig } from "./storageConfig.js";

export const storage: StorageConfig = resolveStorage(process.env);
