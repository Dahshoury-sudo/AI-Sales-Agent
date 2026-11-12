| Phase | Role | Calls | Prompt tok | Cached tok | Completion tok | Cost USD | Mean latency s | p95 latency s | Statuses |
|---|---|---|---|---|---|---|---|---|---|
| ALL | ALL | 6913 | 43017768 | 28690159 | 1478407 | 2.999226 | 3.16 | 8.25 | complete=6897, estimated_unmetered=1, in_flight=2, uncertain_charged_maximum=13 |
| ALL | judge | 326 | 3450805 | 446899 | 1031951 | 0.960187 | 30.93 | 62.94 | complete=321, in_flight=1, uncertain_charged_maximum=4 |
| ALL | seller | 4838 | 37852184 | 28243260 | 393406 | 1.717699 | 1.97 | 4.22 | complete=4831, in_flight=1, uncertain_charged_maximum=6 |
| ALL | simulator | 1748 | 1714779 | 0 | 53050 | 0.221340 | 1.36 | 1.86 | complete=1745, uncertain_charged_maximum=3 |
| ALL | unit-tests | 1 | 0 | 0 | 0 | 0.100000 | None | None | estimated_unmetered=1 |
| baseline | ALL | 586 | 3842136 | 2224753 | 275578 | 0.360282 | 5.77 | 35.12 | complete=586 |
| baseline | judge | 60 | 746191 | 77723 | 239137 | 0.203900 | 38.19 | 72.05 | complete=60 |
| baseline | seller | 394 | 2968399 | 2147030 | 32340 | 0.140251 | 2.31 | 6.56 | complete=394 |
| baseline | simulator | 132 | 127546 | 0 | 4101 | 0.016132 | 1.36 | 1.79 | complete=132 |
| debug-after-final | ALL | 115 | 760369 | 562930 | 25548 | 0.042767 | 2.92 | 6.41 | complete=115 |
| debug-after-final | judge | 4 | 43357 | 5348 | 17143 | 0.013376 | 35.3 | 65.19 | complete=4 |
| debug-after-final | seller | 85 | 690760 | 557582 | 7658 | 0.026046 | 1.86 | 4.71 | complete=85 |
| debug-after-final | simulator | 26 | 26252 | 0 | 747 | 0.003346 | 1.4 | 2.75 | complete=26 |
| debug-after-final-2 | ALL | 54 | 365186 | 266237 | 10456 | 0.020041 | 2.96 | 19.39 | complete=54 |
| debug-after-final-2 | judge | 2 | 21770 | 2674 | 6605 | 0.005716 | 27.26 | 28.41 | complete=2 |
| debug-after-final-2 | seller | 38 | 330216 | 263563 | 3336 | 0.012632 | 2.27 | 3.79 | complete=38 |
| debug-after-final-2 | simulator | 14 | 13200 | 0 | 515 | 0.001693 | 1.35 | 1.64 | complete=14 |
| debug-after-fixes | ALL | 1254 | 7981393 | 5139462 | 248346 | 0.526424 | 2.86 | 6.29 | complete=1254 |
| debug-after-fixes | judge | 47 | 480890 | 61502 | 161628 | 0.133849 | 30.28 | 58.78 | complete=47 |
| debug-after-fixes | seller | 904 | 7201993 | 5077960 | 77192 | 0.354712 | 1.94 | 4.1 | complete=904 |
| debug-after-fixes | simulator | 303 | 298510 | 0 | 9526 | 0.037863 | 1.32 | 1.72 | complete=303 |
| debug-claims-before | ALL | 120 | 631489 | 464780 | 8642 | 0.029357 | 1.78 | 3.34 | complete=120 |
| debug-claims-before | seller | 90 | 602064 | 464780 | 7581 | 0.025592 | 1.9 | 3.5 | complete=90 |
| debug-claims-before | simulator | 30 | 29425 | 0 | 1061 | 0.003765 | 1.39 | 1.85 | complete=30 |
| final | ALL | 886 | 5586409 | 3799171 | 183943 | 0.349711 | 2.82 | 7.46 | complete=886 |
| final | judge | 40 | 414339 | 55908 | 128976 | 0.109848 | 27.37 | 60.52 | complete=40 |
| final | seller | 614 | 4948746 | 3743263 | 48046 | 0.211965 | 1.78 | 4.1 | complete=614 |
| final | simulator | 232 | 223324 | 0 | 6921 | 0.027898 | 1.33 | 1.89 | complete=232 |
| noise-check | ALL | 5 | 32554 | 14246 | 17305 | 0.011083 | 35.48 | 72.25 | complete=5 |
| noise-check | judge | 5 | 32554 | 14246 | 17305 | 0.011083 | 35.48 | 72.25 | complete=5 |
| pilot | ALL | 43 | 227559 | 111061 | 10779 | 0.020942 | 4.62 | 10.68 | complete=43 |
| pilot | judge | 2 | 17276 | 1279 | 7716 | 0.005870 | 46.45 | 76.16 | complete=2 |
| pilot | seller | 30 | 199747 | 109782 | 2787 | 0.013735 | 2.93 | 7.34 | complete=30 |
| pilot | simulator | 11 | 10536 | 0 | 276 | 0.001337 | 1.62 | 2.17 | complete=11 |
| round2-batch1 | ALL | 171 | 982568 | 585495 | 50593 | 0.080151 | 3.73 | 9.5 | complete=171 |
| round2-batch1 | judge | 7 | 65827 | 8340 | 35147 | 0.024842 | 46.06 | 69.88 | complete=7 |
| round2-batch1 | seller | 118 | 869994 | 577155 | 13971 | 0.049322 | 2.17 | 6.34 | complete=118 |
| round2-batch1 | simulator | 46 | 46747 | 0 | 1475 | 0.005987 | 1.31 | 1.68 | complete=46 |
| round2-batch2 | ALL | 291 | 1829833 | 1177563 | 69747 | 0.127362 | 3.28 | 6.97 | complete=291 |
| round2-batch2 | judge | 10 | 100132 | 13900 | 44549 | 0.033192 | 41.88 | 79.52 | complete=10 |
| round2-batch2 | seller | 209 | 1651676 | 1163663 | 22705 | 0.083944 | 2.11 | 4.32 | complete=209 |
| round2-batch2 | simulator | 72 | 78025 | 0 | 2493 | 0.010226 | 1.34 | 1.63 | complete=72 |
| round2-batch3 | ALL | 191 | 1179430 | 833255 | 37507 | 0.069770 | 3.14 | 6.68 | complete=191 |
| round2-batch3 | judge | 7 | 67246 | 9730 | 23825 | 0.019199 | 35.2 | 45.07 | complete=7 |
| round2-batch3 | seller | 137 | 1061963 | 823525 | 12105 | 0.044041 | 2.11 | 5.34 | complete=137 |
| round2-batch3 | simulator | 47 | 50221 | 0 | 1577 | 0.006529 | 1.35 | 1.81 | complete=47 |
| round2-batch4 | ALL | 326 | 1853066 | 1301656 | 47743 | 0.212260 | 2.73 | 5.67 | complete=313, uncertain_charged_maximum=13 |
| round2-batch4 | judge | 15 | 135953 | 15290 | 27766 | 0.093396 | 27.53 | 44.06 | complete=11, uncertain_charged_maximum=4 |
| round2-batch4 | seller | 227 | 1637857 | 1286366 | 17552 | 0.103963 | 1.96 | 3.71 | complete=221, uncertain_charged_maximum=6 |
| round2-batch4 | simulator | 84 | 79256 | 0 | 2425 | 0.014901 | 1.47 | 1.95 | complete=81, uncertain_charged_maximum=3 |
| round2-batch5 | ALL | 99 | 627647 | 428670 | 22379 | 0.039952 | 3.54 | 6.09 | complete=99 |
| round2-batch5 | judge | 4 | 40852 | 5560 | 16295 | 0.012614 | 42.75 | 59.8 | complete=4 |
| round2-batch5 | seller | 70 | 561296 | 423110 | 5324 | 0.024130 | 2.06 | 4.53 | complete=70 |
| round2-batch5 | simulator | 25 | 25499 | 0 | 760 | 0.003208 | 1.42 | 1.86 | complete=25 |
| round2-full | ALL | 2771 | 17118129 | 11780880 | 469841 | 1.009123 | 2.84 | 6.18 | complete=2769, in_flight=2 |
| round2-full | judge | 123 | 1284418 | 175399 | 305859 | 0.293302 | 26.17 | 51.76 | complete=122, in_flight=1 |
| round2-full | seller | 1922 | 15127473 | 11605481 | 142809 | 0.627366 | 1.92 | 3.93 | complete=1921, in_flight=1 |
| round2-full | simulator | 726 | 706238 | 0 | 21173 | 0.088456 | 1.36 | 1.87 | complete=726 |
| unmetered-test-calls | ALL | 1 | 0 | 0 | 0 | 0.100000 | None | None | estimated_unmetered=1 |
| unmetered-test-calls | unit-tests | 1 | 0 | 0 | 0 | 0.100000 | None | None | estimated_unmetered=1 |

Cap $4.00; accounted $2.999226; remaining $0.983082; models: ['gpt-6-luna']; runs: 33
