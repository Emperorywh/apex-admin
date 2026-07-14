/*
 * Domain 错误只表达纯领域不变量，不携带 HTTP 或 ORM 语义。
 * Application 会在具体用例边界将其转换为稳定业务错误。
 */
export class DomainInvariantError extends Error {
  constructor(
    readonly invariant: string,
    message: string,
  ) {
    super(message);
    this.name = 'DomainInvariantError';
  }
}
