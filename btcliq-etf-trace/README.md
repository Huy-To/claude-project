# Spot Bitcoin ETF wallets, traced on-chain (2026-11-15)

No issuer except Bitwise publishes its wallets, and the sites that label them (Arkham, Dune,
BitInfoCharts, Timechain Index) need a login or refuse automated reading. Everything here was
traced from public blockchain data. The result is `../btcliq-etf-wallets.json`, in the format
btcliq's ETF Flows tab reads (`data/etf_wallets.json`).

| Fund | Addresses | Bitcoin in them | Share of the fund | How sure |
|---|---|---|---|---|
| IBIT | 2,956 (1,389 proven, 1,567 by pattern) | about 775,700 (305,900 proven) | about 96% | Proven part: certain. Pattern part: most are IBIT, some are not (see below). 6 slots moved to BTC on 2026-11-15 |
| BTC (Grayscale Mini) | 204 (105 holding) | about 46,865 | about 75% | High: tied to the 2024 spin-off and to Mini's own basket grid (see below) |
| FBTC | 353 (5 creation addresses, 348 cold storage) | about 120,800 | about 69% | Creation addresses: certain. Cold storage: Fidelity's, shared with its other custody clients |
| ARKB | 349 (54 holding) | about 12,950 | about 44% | High for every address listed; the rest of the fund's vaults were not reached |
| BITB | 65 | 36,698 | 100% | Published by Bitwise; btcliq reads it itself |
| GBTC, HODL, BRRR, EZBC, BTCO, BTCW | none | | | Not traced (see "Not traced") |

Fund sizes used for the shares: IBIT about 808,000 BTC at the snapshot, FBTC about 175,750,
ARKB about 29,500 (shares outstanding times bitcoin per share).

## Method

**Share-basket fingerprint.** A fund creates and redeems shares in fixed baskets, so the bitcoin
that moves is an exact multiple of (basket size x bitcoin per share), a number that drifts down
slowly with the fee. Amounts that land on that grid on many different days identify a fund.
Bitcoin per share was calibrated from the chain itself: FBTC 326.0595 BTC = 375,000 shares on
2026-11-13; ARKB 285.0287 BTC = 860,000 shares on 2026-11-02; BITB from Bitwise's own figures.

**Starting points.** One FBTC address reported in the news (`bc1qfse9t6...`, checked: it only
forwards basket multiples), and Bitwise's published BITB wallets, whose counterparties led to
a market maker's deposit address (`38ACfx...`) that other funds also redeem to.

### IBIT (BlackRock, Coinbase Custody)

- Every redemption is paid to one address, `36YZXcTVLPdyapYuqXdJEt46oMVB2NrzVv`, open since
  February 2024 (425,685 BTC received). Its daily totals match the news (1,698.00 BTC on
  2026-10-27, 3,214.90 on 2026-10-23) and IBIT's published outflows (2,852 and 5,320 BTC).
- IBIT keeps its bitcoin in single-key addresses of exactly 300 BTC, filled by Coinbase Prime
  hot wallets (mainly `3MqUP6G1daVS5YTD8fz3QgwjZortWwxXFd`) and reused after they empty.
- **Proven (1,390 addresses):** 1,221 have paid the redemption address; 169 more are joined to
  those by a chain of single-wallet moves (Coinbase rotating a slot to a new address).
- **Pattern (1,572 addresses, 471,600 BTC):** 300-BTC slots of the same kind that have never
  redeemed. They come from a public balance-sorted list of all addresses (2026-11-14), which
  has 2,760 such slots. Most of these were first filled between April and November 2025.
- **Left out (272 slots, 81,600 BTC, `ibit_final.json` -> `other`):** same slot size, but tied to
  a wallet that is not IBIT's. Proven + pattern is about 30,000 BTC short of the fund, so some
  of these are probably IBIT too, and a few of the pattern slots probably are not.

### FBTC (Fidelity Digital Assets)

- Creations in kind arrive at five addresses (`3Kix5jApgaEs7AxF4NQoXDspJiGmk8ST7b`: 52 of 52
  deposits are basket multiples; `32oWtYc...` 41 of 41; `3JA3V7e...` 35 of 35; two more above half).
- They are swept into fresh multisig cold-storage addresses in round amounts (250, 500, 1,000
  BTC). Cold storage was collected by co-spending: addresses spent together belong to one wallet.
- That wallet is Fidelity's custody as a whole. It also contains deposit addresses whose amounts
  are not FBTC baskets (other clients), so the cold storage cannot be split per client on-chain.
  Listed: cold addresses holding 20 BTC or more, without the 68 busy non-FBTC deposit addresses.
- The crawl was stopped after it reached about 21,000 mostly empty historical addresses; about
  55,000 BTC of the fund is not in the list.

### ARKB (ARK 21Shares, Coinbase Custody)

- Redemptions are paid to `3Pfmv9Cibqx3daPRGRCnR398NeXsBSS87o`, opened the day before the ETFs
  launched: 132 of 156 deposits are ARKB share multiples.
- Main custody address `bc1qumhetmcr5cuqlkf5garusc3wwwheg43qx8sghh` (260,000 BTC through it),
  fed by the deposit address `1HnzozYtDbjv88SenhosHHKCXVJfSYM2Mx`, with vault addresses it moves
  round amounts to and from. The list is those, every address that has paid the redemption
  address, and addresses joined to them by custody-to-custody moves.

### BTC (Grayscale Bitcoin Mini Trust, Coinbase Custody), added 2026-11-15

- **Spin-off.** On 2024-07-30 transaction `af57822ed8a4c7b84e734483dc2ebc197f02f2ecf0533566eb1716463b1663c7`
  spent 40 coins of GBTC's staging address `3GMdKrRRmto6hNHVEoLA6xTy6xsmU5PyNH`, which had received
  exactly 26,935.84 BTC (the published spin-off amount) from 44 GBTC vaults (`1...` addresses holding
  700 BTC each since 2021, listed in `gbtc_2024_vaults_historical.json`, all empty now). Coinbase Prime's
  hot wallet `3MqUP6...` then peeled 33 outputs of exactly 700 BTC into fresh `bc1q` addresses on
  2024-07-30/31 (`mini_spinoff_peels.json`). Those are Mini's first vaults.
- **Redemption addresses.** `3B1g33p9mWS1YJB57G5ewuSXkV6vQ5cXKz` and `3Dwfb4U5oXgKQVrxE6WH6GXgXqf4PUYaWW`
  are paid only by those vaults and their successors. Nearly every payment is an exact multiple of one
  unit: 66.3585 = 15 units, 70.7862 = 16, 442.45 = 100, 190.0528 = 43, 676.2203 (two payments on
  2026-11-04) = 153, 3,186.86 (2025-11-14) = 720. The unit is about 4.42 BTC and shrinks 0.15% a year,
  exactly Mini's fee; it equals Mini's bitcoin per share (0.000442) times a 10,000-share basket.
- **June 2025 re-keying.** Mini consolidated about 18,000 BTC into new addresses on 2025-06-10..12, each
  transaction marked with 0.001 BTC to the staging address `3GMdKrRR...`.
- **List.** Seeds above, followed forward: whenever a Mini address spends on its own, its round
  (multiple of 50 BTC) outputs and its largest remaining output are Mini's. Payments to the
  redemption addresses, Coinbase Prime, the fee helper `3J7cUj...` and the fee collector `3EXBmHg9...`
  leave the fund. 204 addresses, 105 holding 46,865 BTC of about 62,800 (`mini_final.json`, with
  the reason each joined). Vaults filled by creations that have never spent are not reached.
- **Correction to IBIT.** Six 300-BTC slots in the IBIT list (5 "pattern", 1 "linked") were created on
  2025-07-16 by Mini vaults splitting off 300 BTC each. Grayscale's published flow that day was
  -362 BTC, not -1,800, so the coins stayed in Grayscale. They are now under BTC. IBIT's pattern rule
  can catch Grayscale slots of the same size; no others were found in the IBIT evidence.
- Mini rotates change into fresh addresses. btcliq adopts addresses that pay `redeems_to`, not change
  outputs, so a rotation can show as an outflow until the list is refreshed.

### Not traced

- **GBTC:** its 2024 vaults are known (above) but all were emptied. The rest of the fund was moved to
  addresses that this session could not link: there are no round-balance `1...` vaults left in the
  balance list, the fee collector `3EXBmHg9...` is not GBTC's (amounts too small), and a basket-grid
  scan of 18 days of Coinbase Custody withdrawals could not even recover Mini's known redemptions, so
  it cannot be trusted to find GBTC's.
- **HODL, BRRR, EZBC, BTCO, BTCW:** no free published list exists (checked: issuers, Dune Spellbook on
  GitHub, news; Arkham and similar need a login). Their redemptions go to shared market-maker
  addresses, where amounts alone do not separate them (ARKB and BTCO, for example, have almost the
  same bitcoin per share times three), and they redeem too rarely for 18 days of data to calibrate.
  Tracing them would need months of Coinbase Custody history (143,000 transactions through the fee
  helper alone) under public-API rate limits.

## Files

- `ibit_final.json`: `confirmed`, `linked`, `pattern`, `other` address lists and balances.
- `ibit_slots.jsonl`, `ibit_sources.jsonl`: each candidate's funders and payees (the evidence).
- `fbtc_state.json`: the co-spending cluster with the reason each address joined.
- `arkb_final.json`: each address with the reason it is listed.
- `summary.json`: the counts above.
- `mini_final.json`: BTC (Mini) addresses with the reason each joined, and balances.
- `mini_spinoff_peels.json`: the 2024-07-30/31 spin-off peel chain outputs.
- `gbtc_2024_vaults_historical.json`: GBTC's 44 vaults that funded the spin-off (empty now).
- `mini.py`, `mp.py`: the Mini crawler and its cached mempool.space client.

## Sources of data

mempool.space and blockstream.info (transactions), blockchain.info (balances and batched
histories), addresses.loyce.club (balance-sorted address list, first 3 MB only),
CoinMarketCap and bitbo.io (share prices and published flows), bitbetf.com (BITB wallets).
