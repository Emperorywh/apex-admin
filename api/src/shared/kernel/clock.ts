/*
 * 时间通过端口显式注入，确保状态机与测试不依赖真实系统时钟。
 * Domain 只接收 Date 值，不读取全局时间。
 */
export abstract class Clock {
  abstract now(): Date;
}

export class SystemClock extends Clock {
  now(): Date {
    return new Date();
  }
}
