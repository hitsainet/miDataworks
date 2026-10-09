// A probe-judge agreement report (FR-009.52 - FR-009.56): "compared with a judge", never "beats".
export interface AgreementFigures {
  rows_compared: number;
  probe_auroc: { value: number; ci_low: number; ci_high: number; n: number } | null;
  judge_auroc: { value: number; ci_low: number; ci_high: number; n: number } | null;
  agreement: number | null;
  kappa: number | null;
  n_agreement: number;
  caveat: string | null;
}

const a = (x: AgreementFigures['probe_auroc']) => (x ? `${x.value.toFixed(4)} [${x.ci_low.toFixed(4)}, ${x.ci_high.toFixed(4)}], n = ${x.n.toLocaleString('en-US')}` : 'not available');

export function AgreementReportView({ figures }: { figures: AgreementFigures }) {
  return (
    <div className="text-sm" data-testid="agreement-report">
      <p>Probe compared with a judge on {figures.rows_compared.toLocaleString('en-US')} rows.</p>
      <p>Probe AUROC {a(figures.probe_auroc)}; judge AUROC {a(figures.judge_auroc)}.</p>
      <p>Agreement {figures.agreement == null ? '—' : `${(figures.agreement * 100).toFixed(1)}%`} (kappa {figures.kappa ?? '—'}, n = {figures.n_agreement.toLocaleString('en-US')}).</p>
      {figures.caveat && <p className="text-amber-800 dark:text-amber-200">{figures.caveat}</p>}
    </div>
  );
}
