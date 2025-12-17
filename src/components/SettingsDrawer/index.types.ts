export type ThemeMode = 'light' | 'dark';
export type LayoutMode = 'side' | 'top';

export interface SettingsState {
  themeMode: ThemeMode;
  layoutMode: LayoutMode;
  primaryColor: string;
  fontSize: number;
  compactMode: boolean;
  borderRadius: number;
}

export interface SettingsDrawerProps {
  children?: React.ReactNode;
  onSettingChange?: (settings: SettingsState) => void;
}
