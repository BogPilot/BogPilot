#include "car.h"

namespace {
#define DIM 9
#define EDIM 9
#define MEDIM 9
typedef void (*Hfun)(double *, double *, double *);

double mass;

void set_mass(double x){ mass = x;}

double rotational_inertia;

void set_rotational_inertia(double x){ rotational_inertia = x;}

double center_to_front;

void set_center_to_front(double x){ center_to_front = x;}

double center_to_rear;

void set_center_to_rear(double x){ center_to_rear = x;}

double stiffness_front;

void set_stiffness_front(double x){ stiffness_front = x;}

double stiffness_rear;

void set_stiffness_rear(double x){ stiffness_rear = x;}
const static double MAHA_THRESH_25 = 3.8414588206941227;
const static double MAHA_THRESH_24 = 5.991464547107981;
const static double MAHA_THRESH_30 = 3.8414588206941227;
const static double MAHA_THRESH_26 = 3.8414588206941227;
const static double MAHA_THRESH_27 = 3.8414588206941227;
const static double MAHA_THRESH_29 = 3.8414588206941227;
const static double MAHA_THRESH_28 = 3.8414588206941227;
const static double MAHA_THRESH_31 = 3.8414588206941227;

/******************************************************************************
 *                      Code generated with SymPy 1.14.0                      *
 *                                                                            *
 *              See http://www.sympy.org/ for more information.               *
 *                                                                            *
 *                         This file is part of 'ekf'                         *
 ******************************************************************************/
void err_fun(double *nom_x, double *delta_x, double *out_9078350480501897929) {
   out_9078350480501897929[0] = delta_x[0] + nom_x[0];
   out_9078350480501897929[1] = delta_x[1] + nom_x[1];
   out_9078350480501897929[2] = delta_x[2] + nom_x[2];
   out_9078350480501897929[3] = delta_x[3] + nom_x[3];
   out_9078350480501897929[4] = delta_x[4] + nom_x[4];
   out_9078350480501897929[5] = delta_x[5] + nom_x[5];
   out_9078350480501897929[6] = delta_x[6] + nom_x[6];
   out_9078350480501897929[7] = delta_x[7] + nom_x[7];
   out_9078350480501897929[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_5521457300630317978) {
   out_5521457300630317978[0] = -nom_x[0] + true_x[0];
   out_5521457300630317978[1] = -nom_x[1] + true_x[1];
   out_5521457300630317978[2] = -nom_x[2] + true_x[2];
   out_5521457300630317978[3] = -nom_x[3] + true_x[3];
   out_5521457300630317978[4] = -nom_x[4] + true_x[4];
   out_5521457300630317978[5] = -nom_x[5] + true_x[5];
   out_5521457300630317978[6] = -nom_x[6] + true_x[6];
   out_5521457300630317978[7] = -nom_x[7] + true_x[7];
   out_5521457300630317978[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_4231614180576002394) {
   out_4231614180576002394[0] = 1.0;
   out_4231614180576002394[1] = 0.0;
   out_4231614180576002394[2] = 0.0;
   out_4231614180576002394[3] = 0.0;
   out_4231614180576002394[4] = 0.0;
   out_4231614180576002394[5] = 0.0;
   out_4231614180576002394[6] = 0.0;
   out_4231614180576002394[7] = 0.0;
   out_4231614180576002394[8] = 0.0;
   out_4231614180576002394[9] = 0.0;
   out_4231614180576002394[10] = 1.0;
   out_4231614180576002394[11] = 0.0;
   out_4231614180576002394[12] = 0.0;
   out_4231614180576002394[13] = 0.0;
   out_4231614180576002394[14] = 0.0;
   out_4231614180576002394[15] = 0.0;
   out_4231614180576002394[16] = 0.0;
   out_4231614180576002394[17] = 0.0;
   out_4231614180576002394[18] = 0.0;
   out_4231614180576002394[19] = 0.0;
   out_4231614180576002394[20] = 1.0;
   out_4231614180576002394[21] = 0.0;
   out_4231614180576002394[22] = 0.0;
   out_4231614180576002394[23] = 0.0;
   out_4231614180576002394[24] = 0.0;
   out_4231614180576002394[25] = 0.0;
   out_4231614180576002394[26] = 0.0;
   out_4231614180576002394[27] = 0.0;
   out_4231614180576002394[28] = 0.0;
   out_4231614180576002394[29] = 0.0;
   out_4231614180576002394[30] = 1.0;
   out_4231614180576002394[31] = 0.0;
   out_4231614180576002394[32] = 0.0;
   out_4231614180576002394[33] = 0.0;
   out_4231614180576002394[34] = 0.0;
   out_4231614180576002394[35] = 0.0;
   out_4231614180576002394[36] = 0.0;
   out_4231614180576002394[37] = 0.0;
   out_4231614180576002394[38] = 0.0;
   out_4231614180576002394[39] = 0.0;
   out_4231614180576002394[40] = 1.0;
   out_4231614180576002394[41] = 0.0;
   out_4231614180576002394[42] = 0.0;
   out_4231614180576002394[43] = 0.0;
   out_4231614180576002394[44] = 0.0;
   out_4231614180576002394[45] = 0.0;
   out_4231614180576002394[46] = 0.0;
   out_4231614180576002394[47] = 0.0;
   out_4231614180576002394[48] = 0.0;
   out_4231614180576002394[49] = 0.0;
   out_4231614180576002394[50] = 1.0;
   out_4231614180576002394[51] = 0.0;
   out_4231614180576002394[52] = 0.0;
   out_4231614180576002394[53] = 0.0;
   out_4231614180576002394[54] = 0.0;
   out_4231614180576002394[55] = 0.0;
   out_4231614180576002394[56] = 0.0;
   out_4231614180576002394[57] = 0.0;
   out_4231614180576002394[58] = 0.0;
   out_4231614180576002394[59] = 0.0;
   out_4231614180576002394[60] = 1.0;
   out_4231614180576002394[61] = 0.0;
   out_4231614180576002394[62] = 0.0;
   out_4231614180576002394[63] = 0.0;
   out_4231614180576002394[64] = 0.0;
   out_4231614180576002394[65] = 0.0;
   out_4231614180576002394[66] = 0.0;
   out_4231614180576002394[67] = 0.0;
   out_4231614180576002394[68] = 0.0;
   out_4231614180576002394[69] = 0.0;
   out_4231614180576002394[70] = 1.0;
   out_4231614180576002394[71] = 0.0;
   out_4231614180576002394[72] = 0.0;
   out_4231614180576002394[73] = 0.0;
   out_4231614180576002394[74] = 0.0;
   out_4231614180576002394[75] = 0.0;
   out_4231614180576002394[76] = 0.0;
   out_4231614180576002394[77] = 0.0;
   out_4231614180576002394[78] = 0.0;
   out_4231614180576002394[79] = 0.0;
   out_4231614180576002394[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_2030839165327973644) {
   out_2030839165327973644[0] = state[0];
   out_2030839165327973644[1] = state[1];
   out_2030839165327973644[2] = state[2];
   out_2030839165327973644[3] = state[3];
   out_2030839165327973644[4] = state[4];
   out_2030839165327973644[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8000000000000007*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_2030839165327973644[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_2030839165327973644[7] = state[7];
   out_2030839165327973644[8] = state[8];
}
void F_fun(double *state, double dt, double *out_1186044288085302440) {
   out_1186044288085302440[0] = 1;
   out_1186044288085302440[1] = 0;
   out_1186044288085302440[2] = 0;
   out_1186044288085302440[3] = 0;
   out_1186044288085302440[4] = 0;
   out_1186044288085302440[5] = 0;
   out_1186044288085302440[6] = 0;
   out_1186044288085302440[7] = 0;
   out_1186044288085302440[8] = 0;
   out_1186044288085302440[9] = 0;
   out_1186044288085302440[10] = 1;
   out_1186044288085302440[11] = 0;
   out_1186044288085302440[12] = 0;
   out_1186044288085302440[13] = 0;
   out_1186044288085302440[14] = 0;
   out_1186044288085302440[15] = 0;
   out_1186044288085302440[16] = 0;
   out_1186044288085302440[17] = 0;
   out_1186044288085302440[18] = 0;
   out_1186044288085302440[19] = 0;
   out_1186044288085302440[20] = 1;
   out_1186044288085302440[21] = 0;
   out_1186044288085302440[22] = 0;
   out_1186044288085302440[23] = 0;
   out_1186044288085302440[24] = 0;
   out_1186044288085302440[25] = 0;
   out_1186044288085302440[26] = 0;
   out_1186044288085302440[27] = 0;
   out_1186044288085302440[28] = 0;
   out_1186044288085302440[29] = 0;
   out_1186044288085302440[30] = 1;
   out_1186044288085302440[31] = 0;
   out_1186044288085302440[32] = 0;
   out_1186044288085302440[33] = 0;
   out_1186044288085302440[34] = 0;
   out_1186044288085302440[35] = 0;
   out_1186044288085302440[36] = 0;
   out_1186044288085302440[37] = 0;
   out_1186044288085302440[38] = 0;
   out_1186044288085302440[39] = 0;
   out_1186044288085302440[40] = 1;
   out_1186044288085302440[41] = 0;
   out_1186044288085302440[42] = 0;
   out_1186044288085302440[43] = 0;
   out_1186044288085302440[44] = 0;
   out_1186044288085302440[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_1186044288085302440[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_1186044288085302440[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_1186044288085302440[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_1186044288085302440[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_1186044288085302440[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_1186044288085302440[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_1186044288085302440[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_1186044288085302440[53] = -9.8000000000000007*dt;
   out_1186044288085302440[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_1186044288085302440[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_1186044288085302440[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_1186044288085302440[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_1186044288085302440[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_1186044288085302440[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_1186044288085302440[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_1186044288085302440[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_1186044288085302440[62] = 0;
   out_1186044288085302440[63] = 0;
   out_1186044288085302440[64] = 0;
   out_1186044288085302440[65] = 0;
   out_1186044288085302440[66] = 0;
   out_1186044288085302440[67] = 0;
   out_1186044288085302440[68] = 0;
   out_1186044288085302440[69] = 0;
   out_1186044288085302440[70] = 1;
   out_1186044288085302440[71] = 0;
   out_1186044288085302440[72] = 0;
   out_1186044288085302440[73] = 0;
   out_1186044288085302440[74] = 0;
   out_1186044288085302440[75] = 0;
   out_1186044288085302440[76] = 0;
   out_1186044288085302440[77] = 0;
   out_1186044288085302440[78] = 0;
   out_1186044288085302440[79] = 0;
   out_1186044288085302440[80] = 1;
}
void h_25(double *state, double *unused, double *out_8437661631283326117) {
   out_8437661631283326117[0] = state[6];
}
void H_25(double *state, double *unused, double *out_672077644608662446) {
   out_672077644608662446[0] = 0;
   out_672077644608662446[1] = 0;
   out_672077644608662446[2] = 0;
   out_672077644608662446[3] = 0;
   out_672077644608662446[4] = 0;
   out_672077644608662446[5] = 0;
   out_672077644608662446[6] = 1;
   out_672077644608662446[7] = 0;
   out_672077644608662446[8] = 0;
}
void h_24(double *state, double *unused, double *out_3168639668644077975) {
   out_3168639668644077975[0] = state[4];
   out_3168639668644077975[1] = state[5];
}
void H_24(double *state, double *unused, double *out_2849292068215812419) {
   out_2849292068215812419[0] = 0;
   out_2849292068215812419[1] = 0;
   out_2849292068215812419[2] = 0;
   out_2849292068215812419[3] = 0;
   out_2849292068215812419[4] = 1;
   out_2849292068215812419[5] = 0;
   out_2849292068215812419[6] = 0;
   out_2849292068215812419[7] = 0;
   out_2849292068215812419[8] = 0;
   out_2849292068215812419[9] = 0;
   out_2849292068215812419[10] = 0;
   out_2849292068215812419[11] = 0;
   out_2849292068215812419[12] = 0;
   out_2849292068215812419[13] = 0;
   out_2849292068215812419[14] = 1;
   out_2849292068215812419[15] = 0;
   out_2849292068215812419[16] = 0;
   out_2849292068215812419[17] = 0;
}
void h_30(double *state, double *unused, double *out_1666826404932975181) {
   out_1666826404932975181[0] = state[4];
}
void H_30(double *state, double *unused, double *out_3190410603115911073) {
   out_3190410603115911073[0] = 0;
   out_3190410603115911073[1] = 0;
   out_3190410603115911073[2] = 0;
   out_3190410603115911073[3] = 0;
   out_3190410603115911073[4] = 1;
   out_3190410603115911073[5] = 0;
   out_3190410603115911073[6] = 0;
   out_3190410603115911073[7] = 0;
   out_3190410603115911073[8] = 0;
}
void h_26(double *state, double *unused, double *out_3334846862727870464) {
   out_3334846862727870464[0] = state[7];
}
void H_26(double *state, double *unused, double *out_3069425674265393778) {
   out_3069425674265393778[0] = 0;
   out_3069425674265393778[1] = 0;
   out_3069425674265393778[2] = 0;
   out_3069425674265393778[3] = 0;
   out_3069425674265393778[4] = 0;
   out_3069425674265393778[5] = 0;
   out_3069425674265393778[6] = 0;
   out_3069425674265393778[7] = 1;
   out_3069425674265393778[8] = 0;
}
void h_27(double *state, double *unused, double *out_5007409174765379854) {
   out_5007409174765379854[0] = state[3];
}
void H_27(double *state, double *unused, double *out_1015647291315486162) {
   out_1015647291315486162[0] = 0;
   out_1015647291315486162[1] = 0;
   out_1015647291315486162[2] = 0;
   out_1015647291315486162[3] = 1;
   out_1015647291315486162[4] = 0;
   out_1015647291315486162[5] = 0;
   out_1015647291315486162[6] = 0;
   out_1015647291315486162[7] = 0;
   out_1015647291315486162[8] = 0;
}
void h_29(double *state, double *unused, double *out_1369959646511357504) {
   out_1369959646511357504[0] = state[1];
}
void H_29(double *state, double *unused, double *out_3700641947430303257) {
   out_3700641947430303257[0] = 0;
   out_3700641947430303257[1] = 1;
   out_3700641947430303257[2] = 0;
   out_3700641947430303257[3] = 0;
   out_3700641947430303257[4] = 0;
   out_3700641947430303257[5] = 0;
   out_3700641947430303257[6] = 0;
   out_3700641947430303257[7] = 0;
   out_3700641947430303257[8] = 0;
}
void h_28(double *state, double *unused, double *out_5950919027667042289) {
   out_5950919027667042289[0] = state[0];
}
void H_28(double *state, double *unused, double *out_1381757069639227317) {
   out_1381757069639227317[0] = 1;
   out_1381757069639227317[1] = 0;
   out_1381757069639227317[2] = 0;
   out_1381757069639227317[3] = 0;
   out_1381757069639227317[4] = 0;
   out_1381757069639227317[5] = 0;
   out_1381757069639227317[6] = 0;
   out_1381757069639227317[7] = 0;
   out_1381757069639227317[8] = 0;
}
void h_31(double *state, double *unused, double *out_8558451049797342332) {
   out_8558451049797342332[0] = state[8];
}
void H_31(double *state, double *unused, double *out_3695633776498745254) {
   out_3695633776498745254[0] = 0;
   out_3695633776498745254[1] = 0;
   out_3695633776498745254[2] = 0;
   out_3695633776498745254[3] = 0;
   out_3695633776498745254[4] = 0;
   out_3695633776498745254[5] = 0;
   out_3695633776498745254[6] = 0;
   out_3695633776498745254[7] = 0;
   out_3695633776498745254[8] = 1;
}
#include <eigen3/Eigen/Dense>
#include <iostream>

typedef Eigen::Matrix<double, DIM, DIM, Eigen::RowMajor> DDM;
typedef Eigen::Matrix<double, EDIM, EDIM, Eigen::RowMajor> EEM;
typedef Eigen::Matrix<double, DIM, EDIM, Eigen::RowMajor> DEM;

void predict(double *in_x, double *in_P, double *in_Q, double dt) {
  typedef Eigen::Matrix<double, MEDIM, MEDIM, Eigen::RowMajor> RRM;

  double nx[DIM] = {0};
  double in_F[EDIM*EDIM] = {0};

  // functions from sympy
  f_fun(in_x, dt, nx);
  F_fun(in_x, dt, in_F);


  EEM F(in_F);
  EEM P(in_P);
  EEM Q(in_Q);

  RRM F_main = F.topLeftCorner(MEDIM, MEDIM);
  P.topLeftCorner(MEDIM, MEDIM) = (F_main * P.topLeftCorner(MEDIM, MEDIM)) * F_main.transpose();
  P.topRightCorner(MEDIM, EDIM - MEDIM) = F_main * P.topRightCorner(MEDIM, EDIM - MEDIM);
  P.bottomLeftCorner(EDIM - MEDIM, MEDIM) = P.bottomLeftCorner(EDIM - MEDIM, MEDIM) * F_main.transpose();

  P = P + dt*Q;

  // copy out state
  memcpy(in_x, nx, DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
}

// note: extra_args dim only correct when null space projecting
// otherwise 1
template <int ZDIM, int EADIM, bool MAHA_TEST>
void update(double *in_x, double *in_P, Hfun h_fun, Hfun H_fun, Hfun Hea_fun, double *in_z, double *in_R, double *in_ea, double MAHA_THRESHOLD) {
  typedef Eigen::Matrix<double, ZDIM, ZDIM, Eigen::RowMajor> ZZM;
  typedef Eigen::Matrix<double, ZDIM, DIM, Eigen::RowMajor> ZDM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, EDIM, Eigen::RowMajor> XEM;
  //typedef Eigen::Matrix<double, EDIM, ZDIM, Eigen::RowMajor> EZM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, 1> X1M;
  typedef Eigen::Matrix<double, Eigen::Dynamic, Eigen::Dynamic, Eigen::RowMajor> XXM;

  double in_hx[ZDIM] = {0};
  double in_H[ZDIM * DIM] = {0};
  double in_H_mod[EDIM * DIM] = {0};
  double delta_x[EDIM] = {0};
  double x_new[DIM] = {0};


  // state x, P
  Eigen::Matrix<double, ZDIM, 1> z(in_z);
  EEM P(in_P);
  ZZM pre_R(in_R);

  // functions from sympy
  h_fun(in_x, in_ea, in_hx);
  H_fun(in_x, in_ea, in_H);
  ZDM pre_H(in_H);

  // get y (y = z - hx)
  Eigen::Matrix<double, ZDIM, 1> pre_y(in_hx); pre_y = z - pre_y;
  X1M y; XXM H; XXM R;
  if (Hea_fun){
    typedef Eigen::Matrix<double, ZDIM, EADIM, Eigen::RowMajor> ZAM;
    double in_Hea[ZDIM * EADIM] = {0};
    Hea_fun(in_x, in_ea, in_Hea);
    ZAM Hea(in_Hea);
    XXM A = Hea.transpose().fullPivLu().kernel();


    y = A.transpose() * pre_y;
    H = A.transpose() * pre_H;
    R = A.transpose() * pre_R * A;
  } else {
    y = pre_y;
    H = pre_H;
    R = pre_R;
  }
  // get modified H
  H_mod_fun(in_x, in_H_mod);
  DEM H_mod(in_H_mod);
  XEM H_err = H * H_mod;

  // Do mahalobis distance test
  if (MAHA_TEST){
    XXM a = (H_err * P * H_err.transpose() + R).inverse();
    double maha_dist = y.transpose() * a * y;
    if (maha_dist > MAHA_THRESHOLD){
      R = 1.0e16 * R;
    }
  }

  // Outlier resilient weighting
  double weight = 1;//(1.5)/(1 + y.squaredNorm()/R.sum());

  // kalman gains and I_KH
  XXM S = ((H_err * P) * H_err.transpose()) + R/weight;
  XEM KT = S.fullPivLu().solve(H_err * P.transpose());
  //EZM K = KT.transpose(); TODO: WHY DOES THIS NOT COMPILE?
  //EZM K = S.fullPivLu().solve(H_err * P.transpose()).transpose();
  //std::cout << "Here is the matrix rot:\n" << K << std::endl;
  EEM I_KH = Eigen::Matrix<double, EDIM, EDIM>::Identity() - (KT.transpose() * H_err);

  // update state by injecting dx
  Eigen::Matrix<double, EDIM, 1> dx(delta_x);
  dx  = (KT.transpose() * y);
  memcpy(delta_x, dx.data(), EDIM * sizeof(double));
  err_fun(in_x, delta_x, x_new);
  Eigen::Matrix<double, DIM, 1> x(x_new);

  // update cov
  P = ((I_KH * P) * I_KH.transpose()) + ((KT.transpose() * R) * KT);

  // copy out state
  memcpy(in_x, x.data(), DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
  memcpy(in_z, y.data(), y.rows() * sizeof(double));
}




}
extern "C" {

void car_update_25(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_25, H_25, NULL, in_z, in_R, in_ea, MAHA_THRESH_25);
}
void car_update_24(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<2, 3, 0>(in_x, in_P, h_24, H_24, NULL, in_z, in_R, in_ea, MAHA_THRESH_24);
}
void car_update_30(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_30, H_30, NULL, in_z, in_R, in_ea, MAHA_THRESH_30);
}
void car_update_26(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_26, H_26, NULL, in_z, in_R, in_ea, MAHA_THRESH_26);
}
void car_update_27(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_27, H_27, NULL, in_z, in_R, in_ea, MAHA_THRESH_27);
}
void car_update_29(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_29, H_29, NULL, in_z, in_R, in_ea, MAHA_THRESH_29);
}
void car_update_28(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_28, H_28, NULL, in_z, in_R, in_ea, MAHA_THRESH_28);
}
void car_update_31(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_31, H_31, NULL, in_z, in_R, in_ea, MAHA_THRESH_31);
}
void car_err_fun(double *nom_x, double *delta_x, double *out_9078350480501897929) {
  err_fun(nom_x, delta_x, out_9078350480501897929);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_5521457300630317978) {
  inv_err_fun(nom_x, true_x, out_5521457300630317978);
}
void car_H_mod_fun(double *state, double *out_4231614180576002394) {
  H_mod_fun(state, out_4231614180576002394);
}
void car_f_fun(double *state, double dt, double *out_2030839165327973644) {
  f_fun(state,  dt, out_2030839165327973644);
}
void car_F_fun(double *state, double dt, double *out_1186044288085302440) {
  F_fun(state,  dt, out_1186044288085302440);
}
void car_h_25(double *state, double *unused, double *out_8437661631283326117) {
  h_25(state, unused, out_8437661631283326117);
}
void car_H_25(double *state, double *unused, double *out_672077644608662446) {
  H_25(state, unused, out_672077644608662446);
}
void car_h_24(double *state, double *unused, double *out_3168639668644077975) {
  h_24(state, unused, out_3168639668644077975);
}
void car_H_24(double *state, double *unused, double *out_2849292068215812419) {
  H_24(state, unused, out_2849292068215812419);
}
void car_h_30(double *state, double *unused, double *out_1666826404932975181) {
  h_30(state, unused, out_1666826404932975181);
}
void car_H_30(double *state, double *unused, double *out_3190410603115911073) {
  H_30(state, unused, out_3190410603115911073);
}
void car_h_26(double *state, double *unused, double *out_3334846862727870464) {
  h_26(state, unused, out_3334846862727870464);
}
void car_H_26(double *state, double *unused, double *out_3069425674265393778) {
  H_26(state, unused, out_3069425674265393778);
}
void car_h_27(double *state, double *unused, double *out_5007409174765379854) {
  h_27(state, unused, out_5007409174765379854);
}
void car_H_27(double *state, double *unused, double *out_1015647291315486162) {
  H_27(state, unused, out_1015647291315486162);
}
void car_h_29(double *state, double *unused, double *out_1369959646511357504) {
  h_29(state, unused, out_1369959646511357504);
}
void car_H_29(double *state, double *unused, double *out_3700641947430303257) {
  H_29(state, unused, out_3700641947430303257);
}
void car_h_28(double *state, double *unused, double *out_5950919027667042289) {
  h_28(state, unused, out_5950919027667042289);
}
void car_H_28(double *state, double *unused, double *out_1381757069639227317) {
  H_28(state, unused, out_1381757069639227317);
}
void car_h_31(double *state, double *unused, double *out_8558451049797342332) {
  h_31(state, unused, out_8558451049797342332);
}
void car_H_31(double *state, double *unused, double *out_3695633776498745254) {
  H_31(state, unused, out_3695633776498745254);
}
void car_predict(double *in_x, double *in_P, double *in_Q, double dt) {
  predict(in_x, in_P, in_Q, dt);
}
void car_set_mass(double x) {
  set_mass(x);
}
void car_set_rotational_inertia(double x) {
  set_rotational_inertia(x);
}
void car_set_center_to_front(double x) {
  set_center_to_front(x);
}
void car_set_center_to_rear(double x) {
  set_center_to_rear(x);
}
void car_set_stiffness_front(double x) {
  set_stiffness_front(x);
}
void car_set_stiffness_rear(double x) {
  set_stiffness_rear(x);
}
}

const EKF car = {
  .name = "car",
  .kinds = { 25, 24, 30, 26, 27, 29, 28, 31 },
  .feature_kinds = {  },
  .f_fun = car_f_fun,
  .F_fun = car_F_fun,
  .err_fun = car_err_fun,
  .inv_err_fun = car_inv_err_fun,
  .H_mod_fun = car_H_mod_fun,
  .predict = car_predict,
  .hs = {
    { 25, car_h_25 },
    { 24, car_h_24 },
    { 30, car_h_30 },
    { 26, car_h_26 },
    { 27, car_h_27 },
    { 29, car_h_29 },
    { 28, car_h_28 },
    { 31, car_h_31 },
  },
  .Hs = {
    { 25, car_H_25 },
    { 24, car_H_24 },
    { 30, car_H_30 },
    { 26, car_H_26 },
    { 27, car_H_27 },
    { 29, car_H_29 },
    { 28, car_H_28 },
    { 31, car_H_31 },
  },
  .updates = {
    { 25, car_update_25 },
    { 24, car_update_24 },
    { 30, car_update_30 },
    { 26, car_update_26 },
    { 27, car_update_27 },
    { 29, car_update_29 },
    { 28, car_update_28 },
    { 31, car_update_31 },
  },
  .Hes = {
  },
  .sets = {
    { "mass", car_set_mass },
    { "rotational_inertia", car_set_rotational_inertia },
    { "center_to_front", car_set_center_to_front },
    { "center_to_rear", car_set_center_to_rear },
    { "stiffness_front", car_set_stiffness_front },
    { "stiffness_rear", car_set_stiffness_rear },
  },
  .extra_routines = {
  },
};

ekf_lib_init(car)
