import { bootstrap } from './bootstrap/bootstrap';

/*
 * main.ts 保持为稳定且无业务逻辑的默认进程入口。
 * 启动失败由 bootstrap 的统一出口设置非零退出状态。
 */
void bootstrap();
