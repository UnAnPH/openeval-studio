import React from 'react';
import clsx from 'clsx';

export interface IonIconProps {
  icon?: any;
  className?: string;
  slot?: string;
  onClick?: React.MouseEventHandler;
  style?: React.CSSProperties;
}

export const IonIcon: React.FC<IonIconProps> = ({ icon: Icon, className = '', style, onClick }) => {
  if (!Icon) return null;
  if (typeof Icon === 'function' || (typeof Icon === 'object' && ('render' in Icon || '$$typeof' in Icon))) {
    const Component = Icon as React.ComponentType<{ className?: string; style?: React.CSSProperties; onClick?: React.MouseEventHandler }>;
    return <Component className={clsx('inline-block shrink-0', className)} style={style} onClick={onClick} />;
  }
  return null;
};

export const IonApp: React.FC<{ children?: React.ReactNode; className?: string }> = ({ children, className }) => (
  <div className={clsx('min-h-screen w-full', className)}>{children}</div>
);

export const IonPage: React.FC<{ children?: React.ReactNode; className?: string }> = ({ children, className }) => (
  <div className={clsx('min-h-screen w-full', className)}>{children}</div>
);

export const IonSpinner: React.FC<{ name?: string; className?: string }> = ({ className = 'w-4 h-4' }) => (
  <svg
    className={clsx('animate-spin inline-block', className)}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
  >
    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
    <path
      className="opacity-75"
      fill="currentColor"
      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
    />
  </svg>
);

export interface IonToastProps {
  isOpen: boolean;
  message: string;
  color?: string;
  duration?: number;
  onDidDismiss?: () => void;
  buttons?: Array<{ text: string; role?: string; handler?: () => void }>;
}

export const IonToast: React.FC<IonToastProps> = ({ isOpen, message, onDidDismiss, buttons }) => {
  if (!isOpen) return null;
  return (
    <div className="fixed bottom-6 right-6 z-50 flex items-center gap-3 bg-red-600 text-white px-4 py-2.5 rounded-xl shadow-lg text-sm font-medium">
      <span>{message}</span>
      {buttons?.map((b, i) => (
        <button
          key={i}
          onClick={() => {
            b.handler?.();
            onDidDismiss?.();
          }}
          className="ml-2 font-bold underline opacity-80 hover:opacity-100 cursor-pointer"
        >
          {b.text}
        </button>
      ))}
    </div>
  );
};

export interface IonModalProps {
  isOpen: boolean;
  onDidDismiss?: () => void;
  className?: string;
  children?: React.ReactNode;
}

export const IonModal: React.FC<IonModalProps> = ({ isOpen, onDidDismiss, children }) => {
  if (!isOpen) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4 overflow-y-auto"
      onClick={onDidDismiss}
    >
      <div
        className="relative w-full max-w-4xl max-h-[90vh] bg-white rounded-2xl shadow-2xl overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
};

export const IonCard: React.FC<{ children?: React.ReactNode; className?: string }> = ({ children, className }) => (
  <div className={className}>{children}</div>
);

export const IonCardHeader: React.FC<{ children?: React.ReactNode; className?: string }> = ({ children, className }) => (
  <div className={className}>{children}</div>
);

export const IonCardTitle: React.FC<{ children?: React.ReactNode; className?: string }> = ({ children, className }) => (
  <div className={className}>{children}</div>
);

export const IonCardContent: React.FC<{ children?: React.ReactNode; className?: string }> = ({ children, className }) => (
  <div className={className}>{children}</div>
);

export const IonBadge: React.FC<{ children?: React.ReactNode; className?: string; color?: string }> = ({
  children,
  className,
}) => (
  <span className={clsx('inline-flex items-center px-2 py-0.5 rounded text-xs font-medium', className)}>
    {children}
  </span>
);

export const IonButton: React.FC<{
  children?: React.ReactNode;
  className?: string;
  color?: string;
  size?: string;
  onClick?: React.MouseEventHandler;
}> = ({ children, className, onClick }) => (
  <button
    onClick={onClick}
    className={clsx(
      'inline-flex items-center justify-center px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-700 text-white font-medium cursor-pointer transition-colors',
      className
    )}
  >
    {children}
  </button>
);

export const IonChip: React.FC<{ children?: React.ReactNode; className?: string }> = ({ children, className }) => (
  <div className={clsx('inline-flex items-center px-2.5 py-1 rounded-full text-xs', className)}>{children}</div>
);

export const setupIonicReact = (_config?: any): void => {};
