import "server-only";
import { validateContract, type ContractName } from "@apex/contracts/validators";
import { HttpError } from "./responses";

export function assertContract<T>(name: ContractName, data: unknown): T {
  const res = validateContract<T>(name, data);
  if (!res.ok) throw new HttpError(422, "CONTRACT_VIOLATION", `${name} failed validation`, { errors: res.errors.slice(0, 8) });
  return res.value;
}
