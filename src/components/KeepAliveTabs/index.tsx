import { Tabs, theme, Popover, Menu } from 'antd';
import type { KeepAliveTabsProps } from './index.types';
import styles from './index.module.css';
import {
	closestCenter,
	DndContext,
	PointerSensor,
	useSensor,
	type DragEndEvent,
} from '@dnd-kit/core';
import { restrictToHorizontalAxis } from '@dnd-kit/modifiers';
import {
	arrayMove,
	horizontalListSortingStrategy,
	SortableContext,
	useSortable,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import React, { useState } from 'react';
import { ReloadOutlined, CloseOutlined, StopOutlined, PicCenterOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

interface DraggableTabPaneProps extends React.HTMLAttributes<HTMLDivElement> {
	'data-node-key': string;
	onRefresh?: (key: string) => void;
	onCloseOthers?: (key: string) => void;
	onCloseAll?: () => void;
	onRemove?: (key: string) => void;
}

const DraggableTabNode: React.FC<Readonly<DraggableTabPaneProps>> = ({ ...props }) => {
	const { token } = theme.useToken();
	const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
		id: props['data-node-key'],
	});

	const [open, setOpen] = useState(false);

	const { t } = useTranslation();

	const style: React.CSSProperties = {
		...props.style,
		transform: CSS.Transform.toString(transform),
		transition,
		cursor: isDragging ? 'grabbing' : 'move',
		zIndex: isDragging ? 1000 : undefined,
		boxShadow: isDragging ? token.boxShadowSecondary : undefined,
		backgroundColor: isDragging ? token.colorBgElevated : undefined,
		borderRadius: isDragging ? token.borderRadius : undefined,
	};

	const handleContextMenu = (e: React.MouseEvent) => {
		e.preventDefault();
		setOpen(true);
	};

	const handleMenuClick = (key: string) => {
		setOpen(false);
		const tabKey = props['data-node-key'];
		switch (key) {
			case 'refresh':
				props.onRefresh?.(tabKey);
				break;
			case 'close':
				props.onRemove?.(tabKey);
				break;
			case 'closeOthers':
				props.onCloseOthers?.(tabKey);
				break;
			case 'closeAll':
				props.onCloseAll?.();
				break;
		}
	};

	const menuContent = (
		<Menu
			onClick={({ key }) => handleMenuClick(key)}
			items={[
				{
					key: 'refresh',
					label: t('刷新此页'),
					icon: <ReloadOutlined />,
				},
				{
					key: 'close',
					label: t('关闭当前'),
					icon: <CloseOutlined />,
				},
				{
					key: 'closeOthers',
					label: t('关闭其他'),
					icon: <PicCenterOutlined />,
				},
				{
					key: 'closeAll',
					label: t('关闭所有'),
					icon: <StopOutlined />,
				},
			]}
		/>
	);

	return (
		<Popover
			content={menuContent}
			trigger={['contextMenu']}
			open={open}
			onOpenChange={setOpen}
			overlayInnerStyle={{ padding: 0 }}
		>
			{React.cloneElement(props.children as React.ReactElement, {
				ref: setNodeRef,
				style,
				onContextMenu: handleContextMenu,
				...attributes,
				...listeners,
			})}
		</Popover>
	);
};

const KeepAliveTabs: React.FC<KeepAliveTabsProps> = (props) => {
	const {
		items = [],
		activeKey,
		onRemove,
		onChange,
		setItems,
		onRefresh,
		onCloseOthers,
		onCloseAll,
	} = props;

	const sensor = useSensor(PointerSensor, { activationConstraint: { distance: 10 } });

	const onDragEnd = ({ active, over }: DragEndEvent) => {
		if (active.id !== over?.id) {
			setItems((prev) => {
				const activeIndex = prev.findIndex((i) => i.key === active.id);
				const overIndex = prev.findIndex((i) => i.key === over?.id);
				return arrayMove(prev, activeIndex, overIndex);
			});
		}
	};

	return (
		<Tabs
			className={styles.tabs}
			hideAdd
			type="editable-card"
			onChange={onChange}
			activeKey={activeKey}
			items={items}
			onEdit={(key, action) => {
				if (action === 'remove') {
					onRemove?.(key as string);
				}
			}}
			renderTabBar={(tabBarProps, DefaultTabBar) => (
				<DndContext
					sensors={[sensor]}
					onDragEnd={onDragEnd}
					collisionDetection={closestCenter}
					modifiers={[restrictToHorizontalAxis]}
				>
					<SortableContext
						items={items.map((i) => i.key)}
						strategy={horizontalListSortingStrategy}
					>
						<DefaultTabBar {...tabBarProps}>
							{(node) => (
								<DraggableTabNode
									{...(node as React.ReactElement<DraggableTabPaneProps>).props}
									key={node.key}
									onRefresh={onRefresh}
									onCloseOthers={onCloseOthers}
									onCloseAll={onCloseAll}
									onRemove={onRemove}
								>
									{node}
								</DraggableTabNode>
							)}
						</DefaultTabBar>
					</SortableContext>
				</DndContext>
			)}
		/>
	);
};

export default KeepAliveTabs;
