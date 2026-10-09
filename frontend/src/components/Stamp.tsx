interface Props {
  kind: 'held' | 'review' | 'approved'
  amount: string
  big?: boolean
  slam?: boolean
}

const WORD = { held: 'HELD', review: 'REVIEW', approved: 'APPROVED' }

/** A rubber stamp: the word says what happened, the figure how much. */
function Stamp({ kind, amount, big = false, slam = false }: Props) {
  return (
    <span className={['stamp', kind, big ? 'big' : '', slam ? 'slam' : ''].join(' ').trim()}>
      <b>{WORD[kind]}</b>
      <span>{amount}</span>
    </span>
  )
}

export default Stamp
