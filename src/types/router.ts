import type { RouteObject } from "react-router";

export interface AppRouteMeta {
	title: string;
	icon?: string;
	code?: string;
	type?: 'menu' | 'action';
}

export type AppRouteObject = Omit<RouteObject, "children" | "handle"> & {
	handle?: AppRouteMeta;
	children?: AppRouteObject[];
};
