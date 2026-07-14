/*
 * 有界信号量限制 Argon2 同时执行数与等待队列长度。
 * 队列已满时立即失败，防止攻击流量无限占用进程内存。
 */
export class BoundedSemaphore {
  private active = 0;
  private readonly waiters: Array<() => void> = [];

  constructor(
    private readonly capacity: number,
    private readonly maximumQueueSize: number,
  ) {}

  async run<T>(work: () => Promise<T>): Promise<T> {
    await this.acquire();
    try {
      return await work();
    } finally {
      this.release();
    }
  }

  private acquire(): Promise<void> {
    if (this.active < this.capacity) {
      this.active += 1;
      return Promise.resolve();
    }
    if (this.waiters.length >= this.maximumQueueSize) {
      return Promise.reject(new SemaphoreQueueFullError());
    }
    return new Promise((resolve) => this.waiters.push(resolve));
  }

  private release(): void {
    const next = this.waiters.shift();
    if (next) {
      next();
      return;
    }
    this.active -= 1;
  }
}

export class SemaphoreQueueFullError extends Error {
  constructor() {
    super('密码哈希并发队列已满');
    this.name = 'SemaphoreQueueFullError';
  }
}
