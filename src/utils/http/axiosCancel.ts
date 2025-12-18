import type { AxiosRequestConfig } from 'axios';

// 用于存储每个请求的标识和取消函数
const pendingMap = new Map<string, AbortController>();

export const getPendingUrl = (config: AxiosRequestConfig) => {
	return [config.method, config.url].join('&');
};

export class AxiosCanceler {
	/**
	 * 添加请求
	 * @param config
	 */
	addPending(config: AxiosRequestConfig) {
		this.removePending(config);
		const url = getPendingUrl(config);
		const controller = new AbortController();
		config.signal = config.signal || controller.signal;
		if (!pendingMap.has(url)) {
			// 如果当前请求在等待中，将其添加到 pendingMap
			pendingMap.set(url, controller);
		}
	}

	/**
	 * 移除请求
	 * @param config
	 */
	removePending(config: AxiosRequestConfig) {
		const url = getPendingUrl(config);
		if (pendingMap.has(url)) {
			// 如果当前请求在等待中，取消它并将其从 pendingMap 中移除
			const controller = pendingMap.get(url);
			if (controller) {
				controller.abort();
			}
			pendingMap.delete(url);
		}
	}

	/**
	 * 清空所有 pending
	 */
	removeAllPending() {
		pendingMap.forEach((controller) => {
			if (controller) {
				controller.abort();
			}
		});
		pendingMap.clear();
	}

	/**
	 * 重置
	 */
	reset() {
		pendingMap.clear();
	}
}
