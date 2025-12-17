export type NotificationType = 'notification' | 'message' | 'event';
export type NotificationGroup = 'all' | 'team';

export interface NotificationItem {
	id: string;
	title: string;
	description: string;
	datetime: string;
	read: boolean;
	type: NotificationType;
	group: NotificationGroup;
	avatar?: string;
}

export interface NotificationListProps {
	data: NotificationItem[];
	onMarkAsRead: (id: string) => void;
}
