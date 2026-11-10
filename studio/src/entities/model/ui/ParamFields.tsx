/**
 * 按 `paramSchema` 渲染的模型参数控件组。
 *
 * 页面只负责「值变了就改 state」，怎么渲染由控件自己按 kind 决定：
 * 枚举 → 下拉、连续数值 → 数字输入、布尔 → 开关、文本 → 输入框。
 * 新增参数不需要动页面，这正是「从 schema 取参数配置」的落点。
 */
import { FormField, Select, Switch, TextInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { parseNumericInput, type ParamControl, type ParamValue } from '../lib/paramForm';

export interface ParamFieldsProps {
  controls: ParamControl[];
  values: Record<string, ParamValue>;
  onChange: (key: string, value: ParamValue) => void;
  /** 多个表单同页时避免 id 撞车 */
  idPrefix?: string;
  className?: string;
}

export function ParamFields({
  controls,
  values,
  onChange,
  idPrefix = 'param',
  className,
}: ParamFieldsProps) {
  if (!controls.length) return null;

  return (
    <div className={cn('grid grid-cols-2 gap-2.5', className)}>
      {controls.map((control) => {
        const id = `${idPrefix}-${control.key}`;
        const value = values[control.key] ?? control.defaultValue;
        const common = {
          label: control.label,
          htmlFor: id,
          ...(control.hint ? { hint: control.hint } : {}),
        };

        if (control.kind === 'bool') {
          return (
            <FormField key={control.key} {...common} className="col-span-2">
              <Switch
                id={id}
                ariaLabel={control.label}
                checked={value === true}
                onChange={(checked) => onChange(control.key, checked)}
              >
                {value === true ? '开启' : '关闭'}
              </Switch>
            </FormField>
          );
        }

        if (control.kind === 'string') {
          return (
            <FormField key={control.key} {...common} className="col-span-2">
              <TextInput
                id={id}
                value={String(value)}
                onChange={(event) => onChange(control.key, event.target.value)}
              />
            </FormField>
          );
        }

        // 有档位就下拉：档位的上下限来自 schema，不是前端猜的
        if (control.options.length) {
          return (
            <FormField key={control.key} {...common}>
              <Select
                block
                id={id}
                aria-label={control.label}
                options={control.options}
                value={String(value)}
                onChange={(event) => {
                  const raw = event.target.value;
                  onChange(control.key, control.kind === 'enum' ? raw : parseNumericInput(raw));
                }}
              />
            </FormField>
          );
        }

        // 连续值：schema 声明了 min/max 就交给原生控件约束，
        // 但仍要允许清空（空 = 不提交，让后端用默认值）
        return (
          <FormField key={control.key} {...common}>
            <TextInput
              id={id}
              type="number"
              aria-label={control.label}
              {...(control.min !== undefined ? { min: control.min } : {})}
              {...(control.max !== undefined ? { max: control.max } : {})}
              {...(control.step !== undefined ? { step: control.step } : {})}
              value={String(value)}
              onChange={(event) => onChange(control.key, parseNumericInput(event.target.value))}
            />
          </FormField>
        );
      })}
    </div>
  );
}
