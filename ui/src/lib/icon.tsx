import clsx from 'clsx';
import type { ComponentType, CSSProperties, MouseEventHandler } from 'react';

type IconType = ComponentType<{
  className?: string;
  style?: CSSProperties;
  onClick?: MouseEventHandler;
  size?: number | string;
}>;

/** Renders a lucide icon at 1em so existing text-* size classes keep working. */
export function Icon({
  icon: IconCmp,
  className = '',
  style,
  onClick,
}: {
  icon?: IconType | null;
  className?: string;
  style?: CSSProperties;
  onClick?: MouseEventHandler;
}) {
  if (!IconCmp) return null;
  return (
    <IconCmp
      size="1em"
      className={clsx('inline-block shrink-0', className)}
      style={style}
      onClick={onClick}
    />
  );
}
