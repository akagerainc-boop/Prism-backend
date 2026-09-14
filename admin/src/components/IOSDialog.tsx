import { useEffect } from 'react';

export interface IOSDialogAction {
  label: string;
  onClick: () => void;
  destructive?: boolean;
  isDefault?: boolean;
}

interface IOSDialogProps {
  open: boolean;
  title: string;
  message?: string;
  icon?: string;
  actions: IOSDialogAction[];
  onDismiss?: () => void;
}

/** iPhone-style centered popup: blurred backdrop, spring-in scale/fade
 * entrance, stacked full-width action buttons separated by hairlines --
 * the shared confirm/detail modal used across the dashboard, matching the
 * same component in the Flutter app (lib/widgets/prism_ios_dialog.dart) so
 * both surfaces feel like one product.
 */
export function IOSDialog({ open, title, message, icon, actions, onDismiss }: IOSDialogProps) {
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onDismiss?.();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onDismiss]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 px-6"
      onClick={onDismiss}
    >
      <div
        className="w-full max-w-sm overflow-hidden rounded-[20px] shadow-2xl backdrop-blur-xl animate-ios-in"
        style={{ background: 'color-mix(in srgb, var(--surface) 92%, transparent)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={`px-6 pt-6 ${message ? 'pb-2.5' : 'pb-4.5'} text-center`}>
          {icon && <div className="mb-2 text-[30px]">{icon}</div>}
          <h2 className="text-[16px] font-extrabold" style={{ color: 'var(--text-primary)' }}>
            {title}
          </h2>
          {message && (
            <p
              className="mt-1.5 text-[12.5px] leading-relaxed"
              style={{ color: 'var(--text-secondary)' }}
            >
              {message}
            </p>
          )}
        </div>
        <div className="border-t" style={{ borderColor: 'var(--divider)' }}>
          {actions.map((action, i) => (
            <button
              key={action.label}
              onClick={action.onClick}
              className={`block w-full py-3.5 text-[15px] transition-colors hover:bg-black/5 dark:hover:bg-white/5 ${
                i > 0 ? 'border-t' : ''
              }`}
              style={{
                borderColor: 'var(--divider)',
                color: action.destructive
                  ? '#c53636'
                  : action.isDefault
                    ? 'var(--prism-primary)'
                    : 'var(--text-primary)',
                fontWeight: action.isDefault ? 800 : 600,
              }}
            >
              {action.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
